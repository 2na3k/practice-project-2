/**
 * Curated demo meal data for the local Remymy experience.
 *
 * Nutrition and preparation times are estimates for demonstration purposes;
 * they are not a substitute for a nutrition label or recipe verification.
 */

export type Meal = {
  id: string;
  title: string;
  description: string;
  cuisine: string;
  minutes: number;
  calories: number;
  protein: number;
  tags: string[];
  ingredients: string[];
  steps: string[];
  image: string;
  color: string;
};

export type Preferences = {
  diet: "any" | "vegetarian" | "vegan" | "halal";
  maxMinutes: number;
  exclude: string[];
  goal: "balanced" | "protein" | "light";
  cuisine: string;
  // most to spend on one meal, in SGD; 0 (or unset) is no limit, as in the Data API
  budget?: number;
};

export const DEFAULT_PREFERENCES: Preferences = {
  diet: "any",
  maxMinutes: 30,
  exclude: [],
  goal: "balanced",
  cuisine: "any",
};

const image = (photoId: string): string =>
  `https://images.unsplash.com/${photoId}?auto=format&fit=crop&w=1200&q=85`;

export const MEAL_DATA_NOTE =
  "Recipe timing and nutrition are estimates; verify them against the source recipe and your portions.";

export const MEALS: Meal[] = [
  {
    id: "sesame-tofu-rice-bowl",
    title: "Sesame Tofu Rice Bowl",
    description:
      "Crisp tofu, edamame, cucumber, and carrots over warm rice with a bright sesame dressing.",
    cuisine: "Asian",
    minutes: 18,
    calories: 540,
    protein: 27,
    tags: [
      "vegan",
      "vegetarian",
      "quick",
      "high-protein",
      "asian",
      "budget-friendly",
    ],
    ingredients: [
      "extra-firm tofu",
      "edamame",
      "brown rice",
      "cucumber",
      "carrot",
      "sesame",
      "gluten-free tamari",
      "lime",
    ],
    steps: [
      "Crisp cubed tofu in a hot pan.",
      "Warm the rice and edamame.",
      "Toss vegetables with sesame, soy, and lime, then assemble.",
    ],
    image: image("photo-1512058564366-18510be2db19"),
    color: "#dcefe7",
  },
  {
    id: "chickpea-shawarma-bowl",
    title: "20-Minute Chickpea Shawarma Bowl",
    description:
      "Spiced chickpeas, tomato, cucumber, and herbs with lemon tahini over fluffy quinoa.",
    cuisine: "Middle Eastern",
    minutes: 20,
    calories: 510,
    protein: 20,
    tags: [
      "vegan",
      "vegetarian",
      "quick",
      "middle eastern",
      "gluten-free",
      "budget-friendly",
    ],
    ingredients: [
      "chickpeas",
      "quinoa",
      "tomato",
      "cucumber",
      "parsley",
      "tahini",
      "lemon",
      "smoked paprika",
    ],
    steps: [
      "Warm chickpeas with paprika and cumin.",
      "Whisk tahini with lemon and water.",
      "Layer quinoa, chickpeas, vegetables, herbs, and dressing.",
    ],
    image: image("photo-1512621776951-a57141f2eefd"),
    color: "#f8e5c2",
  },
  {
    id: "peanut-soba-noodles",
    title: "Spicy Peanut Soba Noodles",
    description:
      "Slippery soba noodles with crunchy vegetables, herbs, and a creamy chili-peanut sauce.",
    cuisine: "Japanese",
    minutes: 15,
    calories: 610,
    protein: 25,
    tags: [
      "vegan",
      "vegetarian",
      "quick",
      "asian",
      "japanese",
      "contains peanuts",
    ],
    ingredients: [
      "100% buckwheat soba",
      "peanut butter",
      "gluten-free tamari",
      "lime",
      "cabbage",
      "bell pepper",
      "scallion",
      "chili",
    ],
    steps: [
      "Boil the soba and rinse under cold water.",
      "Whisk peanut butter, soy, lime, and chili.",
      "Toss noodles and vegetables with the sauce.",
    ],
    image: image("photo-1552611052-33e04de081de"),
    color: "#f4d8c5",
  },
  {
    id: "lemon-herb-chicken-couscous",
    title: "Lemon Herb Chicken Couscous",
    description:
      "Juicy chicken, pearl couscous, spinach, and tomatoes finished with lemon and fresh herbs.",
    cuisine: "Mediterranean",
    minutes: 25,
    calories: 560,
    protein: 42,
    tags: ["high-protein", "mediterranean", "one-pan"],
    ingredients: [
      "chicken breast",
      "pearl couscous",
      "spinach",
      "cherry tomatoes",
      "lemon",
      "parsley",
      "olive oil",
    ],
    steps: [
      "Season and sear the chicken.",
      "Simmer couscous with tomatoes and stock.",
      "Slice the chicken and fold in spinach, lemon, and herbs.",
    ],
    image: image("photo-1532550907401-a500c9a57435"),
    color: "#f5e8bd",
  },
  {
    id: "salmon-sushi-bowl",
    title: "Salmon Sushi Bowl",
    description:
      "Flaky salmon, seasoned rice, avocado, cucumber, and nori with a quick soy-ginger glaze.",
    cuisine: "Japanese",
    minutes: 20,
    calories: 590,
    protein: 36,
    tags: ["high-protein", "quick", "japanese", "asian", "pescatarian"],
    ingredients: [
      "salmon fillet",
      "sushi rice",
      "avocado",
      "cucumber",
      "nori",
      "gluten-free tamari",
      "ginger",
      "rice vinegar",
    ],
    steps: [
      "Cook the salmon until flaky.",
      "Season warm rice with vinegar.",
      "Build bowls with sliced salmon, vegetables, nori, and glaze.",
    ],
    image: image("photo-1547592180-85f173990554"),
    color: "#f7d8cf",
  },
  {
    id: "creamy-mushroom-pasta",
    title: "Creamy Mushroom Pasta",
    description:
      "Silky garlic cream sauce clings to pasta and caramelized mushrooms with parmesan.",
    cuisine: "Italian",
    minutes: 25,
    calories: 700,
    protein: 24,
    tags: [
      "vegetarian",
      "italian",
      "comfort-food",
      "contains dairy",
      "contains gluten",
      "contains mushrooms",
    ],
    ingredients: [
      "rigatoni",
      "mushrooms",
      "heavy cream",
      "parmesan",
      "garlic",
      "shallot",
      "spinach",
    ],
    steps: [
      "Boil pasta until just tender.",
      "Brown mushrooms with garlic and shallot.",
      "Add cream and parmesan, then toss with pasta and spinach.",
    ],
    image: image("photo-1551183053-bf91a1d81141"),
    color: "#eadfc8",
  },
  {
    id: "turkey-taco-skillet",
    title: "Turkey Taco Skillet",
    description:
      "Lean turkey, black beans, peppers, and corn simmered with smoky tomato salsa.",
    cuisine: "Mexican",
    minutes: 20,
    calories: 520,
    protein: 39,
    tags: ["high-protein", "quick", "mexican", "one-pan", "gluten-free"],
    ingredients: [
      "lean ground turkey",
      "black beans",
      "corn",
      "bell pepper",
      "tomato salsa",
      "lime",
      "avocado",
      "cumin",
    ],
    steps: [
      "Brown turkey with cumin.",
      "Add peppers, beans, corn, and salsa.",
      "Simmer until thick and finish with lime and avocado.",
    ],
    image: image("photo-1552332386-f8dd00dc2f85"),
    color: "#f5d4ad",
  },
  {
    id: "lentil-coconut-curry",
    title: "Lentil Coconut Curry",
    description:
      "Red lentils simmered with coconut, tomato, ginger, and warming spices, served with rice.",
    cuisine: "Indian",
    minutes: 30,
    calories: 580,
    protein: 24,
    tags: [
      "vegan",
      "vegetarian",
      "indian",
      "gluten-free",
      "budget-friendly",
      "meal-prep",
    ],
    ingredients: [
      "red lentils",
      "coconut milk",
      "tomato",
      "spinach",
      "ginger",
      "garlic",
      "basmati rice",
      "curry powder",
    ],
    steps: [
      "Sauté ginger, garlic, and curry spices.",
      "Simmer lentils with tomato and coconut milk.",
      "Fold in spinach and serve with rice.",
    ],
    image: image("photo-1601050690597-df0568f70950"),
    color: "#f2d2b5",
  },
  {
    id: "caprese-white-bean-toast",
    title: "Caprese White Bean Toast",
    description:
      "Creamy white beans, tomato, basil, and mozzarella on golden sourdough with balsamic.",
    cuisine: "Italian",
    minutes: 12,
    calories: 430,
    protein: 20,
    tags: [
      "vegetarian",
      "quick",
      "italian",
      "contains dairy",
      "contains gluten",
    ],
    ingredients: [
      "sourdough bread",
      "white beans",
      "tomato",
      "fresh mozzarella",
      "basil",
      "balsamic vinegar",
      "olive oil",
    ],
    steps: [
      "Toast the bread.",
      "Mash beans with olive oil and pepper.",
      "Top with tomato, mozzarella, basil, and balsamic.",
    ],
    image: image("photo-1505577058444-a3dab90d4253"),
    color: "#f6dfc8",
  },
  {
    id: "green-goddess-quinoa-bowl",
    title: "Green Goddess Quinoa Bowl",
    description:
      "Quinoa, edamame, avocado, greens, and cucumber with a zingy herb dressing.",
    cuisine: "American",
    minutes: 18,
    calories: 470,
    protein: 18,
    tags: ["vegan", "vegetarian", "quick", "gluten-free", "light", "american"],
    ingredients: [
      "quinoa",
      "edamame",
      "avocado",
      "arugula",
      "cucumber",
      "pumpkin seeds",
      "lemon",
      "parsley",
    ],
    steps: [
      "Cook quinoa and cool slightly.",
      "Blend herbs, lemon, and olive oil into a dressing.",
      "Toss everything together and top with seeds.",
    ],
    image: image("photo-1540189549336-e6e99c3679fe"),
    color: "#dcebd5",
  },
  {
    id: "kimchi-beef-rice-bowl",
    title: "Kimchi Beef Rice Bowl",
    description:
      "Savory ground beef, tangy kimchi, rice, cucumber, and a jammy egg in a Korean-inspired bowl.",
    cuisine: "Korean",
    minutes: 20,
    calories: 610,
    protein: 35,
    tags: ["high-protein", "quick", "korean", "asian"],
    ingredients: [
      "lean ground beef",
      "kimchi",
      "steamed rice",
      "egg",
      "cucumber",
      "scallion",
      "gluten-free gochujang",
      "sesame",
    ],
    steps: [
      "Brown beef with gochujang.",
      "Warm kimchi and cook the egg.",
      "Serve over rice with cucumber, scallion, and sesame.",
    ],
    image: image("photo-1603133872878-684f208fb84b"),
    color: "#f3d0c5",
  },
  {
    id: "thai-basil-chicken",
    title: "Thai Basil Chicken",
    description:
      "Fast wok-seared chicken with peppers, Thai basil, garlic, and a savory chili sauce.",
    cuisine: "Thai",
    minutes: 18,
    calories: 550,
    protein: 38,
    tags: ["high-protein", "quick", "thai", "asian", "gluten-free"],
    ingredients: [
      "chicken thigh",
      "Thai basil",
      "bell pepper",
      "garlic",
      "bird's eye chili",
      "fish sauce",
      "lime",
      "jasmine rice",
    ],
    steps: [
      "Cook rice and keep warm.",
      "Stir-fry chicken with garlic, chili, and pepper.",
      "Toss in basil and fish sauce, then serve with lime.",
    ],
    image: image("photo-1455619452474-d2be8b1e70cd"),
    color: "#dce9cf",
  },
  {
    id: "chicken-tzatziki-pita",
    title: "Chicken Tzatziki Pita",
    description:
      "Herby chicken, crisp lettuce, tomato, and cooling tzatziki tucked into warm pita.",
    cuisine: "Greek",
    minutes: 20,
    calories: 560,
    protein: 42,
    tags: [
      "high-protein",
      "quick",
      "greek",
      "mediterranean",
      "contains dairy",
      "contains gluten",
    ],
    ingredients: [
      "chicken breast",
      "pita",
      "Greek yogurt",
      "cucumber",
      "tomato",
      "lettuce",
      "dill",
      "lemon",
    ],
    steps: [
      "Season and grill the chicken.",
      "Stir yogurt with cucumber, dill, and lemon.",
      "Fill pita with chicken, vegetables, and tzatziki.",
    ],
    image: image("photo-1529006557810-274b9b2fc783"),
    color: "#e5efd4",
  },
  {
    id: "veggie-tofu-fried-rice",
    title: "Veggie Tofu Fried Rice",
    description:
      "A speedy skillet of rice, tofu, peas, carrots, and scallions with toasted sesame.",
    cuisine: "Chinese",
    minutes: 20,
    calories: 500,
    protein: 22,
    tags: [
      "vegan",
      "vegetarian",
      "quick",
      "asian",
      "chinese",
      "budget-friendly",
    ],
    ingredients: [
      "extra-firm tofu",
      "cooked rice",
      "peas",
      "carrot",
      "scallion",
      "gluten-free tamari",
      "sesame oil",
      "ginger",
    ],
    steps: [
      "Crisp tofu in sesame oil.",
      "Stir-fry carrot, peas, and ginger.",
      "Fold in rice, tofu, and soy sauce until hot.",
    ],
    image: image("photo-1603133872878-684f208fb84b"),
    color: "#e4eacb",
  },
  {
    id: "tomato-basil-pasta",
    title: "Tomato Basil Pasta",
    description:
      "Al dente pasta in a bright tomato, garlic, and basil sauce with a little parmesan.",
    cuisine: "Italian",
    minutes: 18,
    calories: 560,
    protein: 19,
    tags: [
      "vegetarian",
      "quick",
      "italian",
      "comfort-food",
      "contains dairy",
      "contains gluten",
    ],
    ingredients: [
      "spaghetti",
      "crushed tomatoes",
      "garlic",
      "basil",
      "parmesan",
      "olive oil",
      "red pepper flakes",
    ],
    steps: [
      "Boil pasta until al dente.",
      "Simmer tomatoes with garlic and olive oil.",
      "Toss pasta with sauce, basil, and parmesan.",
    ],
    image: image("photo-1473093295043-cdd812d0e601"),
    color: "#f4d3c4",
  },
  {
    id: "roasted-vegetable-hummus-bowl",
    title: "Roasted Vegetable Hummus Bowl",
    description:
      "Warm roasted vegetables, hummus, greens, and quinoa with lemon and toasted seeds.",
    cuisine: "Mediterranean",
    minutes: 15,
    calories: 450,
    protein: 17,
    tags: [
      "vegan",
      "vegetarian",
      "quick",
      "light",
      "mediterranean",
      "gluten-free",
    ],
    ingredients: [
      "hummus",
      "quinoa",
      "zucchini",
      "bell pepper",
      "cherry tomatoes",
      "mixed greens",
      "pumpkin seeds",
      "lemon",
    ],
    steps: [
      "Roast chopped vegetables until browned.",
      "Warm quinoa and season with lemon.",
      "Serve with hummus, greens, vegetables, and seeds.",
    ],
    image: image("photo-1512621776951-a57141f2eefd"),
    color: "#eee2c7",
  },
  {
    id: "tuna-white-bean-salad",
    title: "Tuna White Bean Salad",
    description:
      "Protein-rich tuna and white beans with greens, tomato, celery, and lemon vinaigrette.",
    cuisine: "Mediterranean",
    minutes: 10,
    calories: 390,
    protein: 34,
    tags: [
      "high-protein",
      "quick",
      "light",
      "mediterranean",
      "gluten-free",
      "pescatarian",
    ],
    ingredients: [
      "tuna",
      "white beans",
      "mixed greens",
      "tomato",
      "celery",
      "red onion",
      "lemon",
      "olive oil",
    ],
    steps: [
      "Drain tuna and beans.",
      "Whisk lemon, olive oil, and pepper.",
      "Toss all ingredients together and serve chilled.",
    ],
    image: image("photo-1540189549336-e6e99c3679fe"),
    color: "#dce9df",
  },
];
