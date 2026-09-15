import type { ChatResult } from './chat-types';
import type { Preferences } from './meals';
import { interpretRequest, recommendMeals } from './recommender';

/** An explicitly labeled, offline preview; never impersonates a provider response. */
export function demoReply(text: string, preferences: Preferences, turn = 0): ChatResult {
  if (/^(hi|hey|hello|good morning|good evening)[!.\s]*$/i.test(text.trim())) {
    return { text: "Hey! What sounds good today? Tell me a craving, an ingredient, or how much time you've got, and we'll start there.", preferences, recommendations: [] };
  }
  if (/^(thanks|thank you|cheers)[!.\s]*$/i.test(text.trim())) {
    return { text: "Anytime. Hope your next meal hits the spot! Your shortlist is waiting in Meals whenever you need it.", preferences, recommendations: [] };
  }
  const next = interpretRequest(text, preferences);
  let recommendations = recommendMeals(next, text);
  if (/surprise me/i.test(text) && recommendations.length > 1) {
    const offset = (turn + 1) % recommendations.length;
    recommendations = [...recommendations.slice(offset), ...recommendations.slice(0, offset)];
  }
  if (!recommendations.length) {
    return { text: `I couldn't find a match in this small demo catalog with your ${next.maxMinutes}-minute limit${next.exclude.length ? ` and ingredients to skip (${next.exclude.join(', ')})` : ''}. Want to allow a little more time? You can adjust that in Preferences; I'll keep your other choices.`, preferences: next, recommendations };
  }
  const [first, second] = recommendations;
  const intro = /no |without |avoid /i.test(text) ? "Got it — I'll keep those ingredients off your plate." : /protein/i.test(text) ? "Let's make it filling." : /quick|ready|minutes/i.test(text) ? "Something good, without spending all evening in the kitchen." : /surprise/i.test(text) ? "Let's shake up the usual." : "Here's what I'd put on your shortlist.";
  const response = `${intro}\n\n**${first.meal.title}** would be my first pick: ${first.meal.description} It's ready in **${first.meal.minutes} minutes**.${second ? `\n\nOr try **${second.meal.title}** if you're in more of a ${second.meal.cuisine.toLowerCase()} mood.` : ''}\n\n${next.diet !== 'any' ? `I've kept these ${next.diet}. ` : ''}Anything you'd change — the ingredients, the time, or the kind of food?`;
  return { text: response, preferences: next, recommendations };
}
