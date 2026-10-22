"""Turn a free-text food query into the inputs of the recipe clustering and classification models.

A query describes one food the way the training rows do (FoodData Central descriptions such as
"Waffles, buttermilk, frozen, ready-to-heat"). Nutrients per 100 g and a price are optional and can be written into the
query ("grilled chicken breast, 165 kcal, 31g protein, 3.6g fat, $6.50") or passed as flags. Terms are extracted as the
data pipeline did:
- product: GLiNER spans labelled "product" (2_nutrient_analysis.ipynb), each lowercased, stripped of stop words and
  punctuation and of the item's own adj / verb words (3_process_price.ipynb);
- adj / verb: stanza UPOS tags ADJ / VERB over the description (2_nutrient_analysis.ipynb).

A training food can also be looked up by fdc_id ("--fdc-id 167516", or "fdc:167516" / "167516" as the query) in the
premade api/weights/fdc_lookup.json (build_fdc_lookup.py): it is then used with its exact training inputs, and its
training cluster and labels are shown next to the prediction.
"""
from functools import lru_cache
import json
from pathlib import Path
import re
import sys

import nltk
from nltk.corpus import stopwords

NUTRIENT_COLS = ["protein_g", "fat_g", "carb_g", "energy_kcal", "fiber_g", "sodium_mg",
                 "cholesterol_mg", "satfat_g", "sugars_g"]

# api/weights/fdc_lookup.json: fdc_id → training food, written by build_fdc_lookup.py.
DEFAULT_LOOKUP = Path(__file__).resolve().parents[3] / "weights" / "fdc_lookup.json"
FDC_QUERY = re.compile(r"(?:fdc(?:_id)?\s*[:=#]?\s*)?(\d+)", re.I)

NER_MODEL = "urchade/gliner_large-v2.1"
NER_LABELS = ["brand", "variant", "product"]
NER_THRESHOLD = 0.3
NER_PREFIX = "Food item: "

# Query words → nutrient column. Longest first, so "saturated fat" is not read as "fat".
NUTRIENT_ALIASES = {
    "saturated fat": "satfat_g", "sat fat": "satfat_g", "satfat": "satfat_g", "total fat": "fat_g", "fat": "fat_g",
    "protein": "protein_g", "carbohydrates": "carb_g", "carbohydrate": "carb_g", "carbs": "carb_g", "carb": "carb_g",
    "calories": "energy_kcal", "calorie": "energy_kcal", "kcal": "energy_kcal", "energy": "energy_kcal",
    "cal": "energy_kcal", "fibre": "fiber_g", "fiber": "fiber_g", "sodium": "sodium_mg",
    "cholesterol": "cholesterol_mg", "sugars": "sugars_g", "sugar": "sugars_g",
}
_NAME = "|".join(re.escape(name) for name in sorted(NUTRIENT_ALIASES, key=len, reverse=True))
_NUMBER = r"(?P<num>\d+(?:\.\d+)?)"
_UNIT = r"(?P<unit>mg|g|kcal|kj|cal)"
# "31g protein" / "165 kcal" and "protein: 31g" / "sodium 74 mg"; a query is read in whichever style matches more.
AMOUNT_FIRST = re.compile(rf"{_NUMBER}\s*{_UNIT}?\s*(?:of\s+)?(?P<name>{_NAME})\b", re.I)
NAME_FIRST = re.compile(rf"\b(?P<name>{_NAME})\s*[:=]?\s*{_NUMBER}\s*{_UNIT}?\b", re.I)
PRICE = re.compile(r"(?:\$|\bs\$)\s*(?P<num>\d+(?:\.\d+)?)|(?P<num2>\d+(?:\.\d+)?)\s*(?:sgd|dollars?)\b", re.I)
PER_100G = re.compile(r"\bper\s+100\s*g\b", re.I)

try:
    _ENGLISH = stopwords.words("english")
except LookupError:  # download only when missing: nltk.download checks its index online on every call
    nltk.download("stopwords", quiet=True)
    _ENGLISH = stopwords.words("english")
# 3_process_price.ipynb's STOP_WORDS: NLTK's English list without single letters and negations.
STOP_WORDS = {w for w in _ENGLISH if len(w) > 1} - {"no", "nor", "not"}


def _convert(value, unit, column):
    """An amount in `unit` in the unit of `column` (g, mg or kcal)."""
    unit = (unit or "").lower()
    if column == "energy_kcal":
        return value / 4.184 if unit == "kj" else value
    if unit == "g" and column.endswith("_mg"):
        return value * 1000
    if unit == "mg" and column.endswith("_g"):
        return value / 1000
    return value


def parse_query(text):
    """Split a query into its description, the nutrients written into it and a price ($ / SGD), if any."""
    matches = max([list(AMOUNT_FIRST.finditer(text)), list(NAME_FIRST.finditer(text))], key=len)
    nutrients = {}
    spans = []
    for match in matches:
        column = NUTRIENT_ALIASES[match["name"].lower()]
        nutrients[column] = _convert(float(match["num"]), match["unit"], column)
        spans.append(match.span())
    price = None
    for match in PRICE.finditer(text):
        price = float(match["num"] or match["num2"])
        spans.append(match.span())
    spans += [match.span() for match in PER_100G.finditer(text)]

    description = text
    for start, end in sorted(spans, reverse=True):
        description = description[:start] + " " + description[end:]
    # tidy the separators the removed amounts leave behind
    description = re.sub(r"\s+", " ", description)
    description = re.sub(r"\s*([,;])(?:\s*[,;])+", r"\1", description)
    description = re.sub(r"\s+([,;])", r"\1", description)
    description = re.sub(r"(?:[\s,;:-]|\b(?:with|and|has|contains)\b)+$", "", description, flags=re.I)
    return description.strip(" ,;:-"), nutrients, price


@lru_cache(maxsize=4)
def load_fdc_lookup(path=DEFAULT_LOOKUP):
    """{fdc_id: training food} from the premade lookup, or {} when the file is missing."""
    path = Path(path)
    if not path.exists():
        return {}
    return {int(fdc_id): food for fdc_id, food in json.loads(path.read_text()).items()}


def normalize_description(text):
    """3_process_price.ipynb: lowercase, drop "(...)" asides, punctuation and stop words; no stemming."""
    s = re.sub(r"\(.*?\)", " ", str(text).lower())
    s = re.sub(r"[^a-z0-9%\s]", " ", s)
    return " ".join(w for w in s.split() if w not in STOP_WORDS and w.strip("%"))


def _norm_span(span, skip):
    return " ".join(w for w in normalize_description(span).split() if w not in skip)


class TermExtractor:
    """product / adj / verb terms of a description, with GLiNER and stanza loaded by load() or else on first use."""

    def __init__(self, ner_model=NER_MODEL):
        self.ner_model = ner_model
        self._ner_pipeline = None
        self._pos_pipeline = None

    def load(self):
        """Load GLiNER and stanza now, downloading any not cached yet, rather than on the first description."""
        self._ner()
        self._pos()
        return self

    # held on the instance, not in an lru_cache(maxsize=1) shared by every extractor: two extractors would evict
    # each other's models and reload them on alternate calls
    def _ner(self):
        if self._ner_pipeline is None:
            from gliner import GLiNER
            self._ner_pipeline = GLiNER.from_pretrained(self.ner_model)
        return self._ner_pipeline

    def _pos(self):
        if self._pos_pipeline is None:
            import stanza
            self._pos_pipeline = stanza.Pipeline("en", processors="tokenize,pos", verbose=False)
        return self._pos_pipeline

    def __call__(self, description):
        text = NER_PREFIX + description
        entities = self._ner().inference([text], NER_LABELS, threshold=NER_THRESHOLD, flat_ner=True,
                                         multi_label=True)[0]
        spans = []
        for entity in sorted(entities, key=lambda e: e["start"]):
            if entity["label"] == "product" and entity["start"] >= len(NER_PREFIX) and entity["text"] not in spans:
                spans.append(entity["text"])

        adj, verb = [], []
        for sentence in self._pos()(description).sentences:
            for word in sentence.words:
                if word.upos == "ADJ":
                    adj.append(word.text)
                elif word.upos == "VERB":
                    verb.append(word.text)

        skip = {w.lower() for w in adj + verb}
        product = list(dict.fromkeys(s for s in (_norm_span(span, skip) for span in spans) if s))
        # rows NER left without a product keep the description as their item
        return {"product": product or [_norm_span(description, skip)], "adj": adj, "verb": verb}


def add_query_arguments(parser):
    """The query and per-field override flags shared by both inference scripts."""
    parser.add_argument("query", nargs="*",
                        help='food description, nutrients per 100 g optional, e.g. "Waffles, buttermilk, frozen, '
                             '273 kcal, 6.6g protein", or a training fdc_id. Omit it to type queries one per line.')
    parser.add_argument("--fdc-id", type=int, nargs="+", metavar="ID",
                        help="look up training foods by fdc_id instead of describing them")
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP,
                        help="premade fdc_id lookup (default %(default)s)")
    nutrients = parser.add_argument_group("nutrients per 100 g (override the values written in the query)")
    for column in NUTRIENT_COLS:
        nutrients.add_argument(f"--{column.replace('_', '-')}", dest=column, type=float, metavar="X")
    terms = parser.add_argument_group("terms (skip GLiNER / stanza for the given field)")
    for field in ("product", "adj", "verb"):
        terms.add_argument(f"--{field}", nargs="+", metavar="TERM")
    parser.add_argument("--json", action="store_true", help="print one JSON object per query")


def iter_queries(args):
    """The --fdc-id foods, else the query from the command line, else one query per line of stdin (blank line or
    Ctrl-D ends)."""
    if args.fdc_id:
        yield from (f"fdc:{fdc_id}" for fdc_id in args.fdc_id)
        return
    if args.query:
        yield " ".join(args.query)
        return
    interactive = sys.stdin.isatty()
    while True:
        try:
            line = input("food> " if interactive else "")
        except EOFError:
            return
        if not line.strip():
            return
        yield line.strip()


class UnknownFood(LookupError):
    """An fdc_id that is not a training food in the lookup."""


class EmptyQuery(ValueError):
    """A query with no food description left once its amounts are taken out."""


def lookup_item(fdc_id, nutrients=None, price=None, lookup=DEFAULT_LOOKUP):
    """A training food from the premade lookup, with the given nutrients and price applied on top.
    FileNotFoundError without a lookup file, UnknownFood for an fdc_id it does not hold."""
    foods = load_fdc_lookup(lookup)
    if not foods:
        raise FileNotFoundError(f"no fdc_id lookup at {lookup}; run build_fdc_lookup.py")
    if fdc_id not in foods:
        raise UnknownFood(f"fdc_id {fdc_id} is not a training food")
    food = json.loads(json.dumps(foods[fdc_id]))  # deep copy: the cached lookup stays as built
    food["nutrients"].update({c: v for c, v in (nutrients or {}).items() if v is not None})
    if price is not None:
        food["price"], food["price_interval"] = price, [price, price]
    return {"fdc_id": fdc_id, **food}


def build_item(query, extractor, nutrients=None, terms=None, price=None, lookup=DEFAULT_LOOKUP):
    """The model input of one query: description, product / adj / verb, nutrients (None = unknown) and price.
    `nutrients` ({column: value}), `terms` ({"product" / "adj" / "verb": [...]}) and `price` override what the query
    holds; None values are ignored. A query that is an fdc_id ("167516", "fdc:167516") is looked up instead.
    EmptyQuery when no description is left once the amounts are taken out."""
    nutrients = {c: v for c, v in (nutrients or {}).items() if v is not None}
    given = {field: value for field, value in (terms or {}).items() if value}
    fdc = FDC_QUERY.fullmatch(query.strip())
    if fdc:
        return lookup_item(int(fdc[1]), nutrients, price, lookup)
    description, parsed, parsed_price = parse_query(query)
    if not description:
        raise EmptyQuery(f"no food description left in {query!r} after removing the nutrient amounts")
    parsed.update(nutrients)
    extracted = extractor(description) if len(given) < 3 else {}
    return {"description": description, **extracted, **given,
            "nutrients": {column: parsed.get(column) for column in NUTRIENT_COLS},
            "price": price if price is not None else parsed_price}


def item_from_args(query, args, extractor):
    """build_item with the flags of add_query_arguments (and --price, where a script has it) as overrides."""
    return build_item(query, extractor,
                      nutrients={column: getattr(args, column, None) for column in NUTRIENT_COLS},
                      terms={field: getattr(args, field, None) for field in ("product", "adj", "verb")},
                      price=getattr(args, "price", None), lookup=getattr(args, "lookup", DEFAULT_LOOKUP))
