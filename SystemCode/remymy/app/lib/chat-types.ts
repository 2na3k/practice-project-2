import type { Preferences } from './meals';
import type { MealRecommendation } from './recommender';

export type RegistryModel = { id: string; name: string; contextWindow: number; reasoning: boolean };
export type RegistryProvider = { id: string; name: string; models: RegistryModel[]; supported: boolean; note?: string };
export type ConnectionStatus = { connected: boolean; provider?: string; model?: string; expiresAt?: number };
export type ChatTurn = { role: 'user' | 'assistant'; text: string };
export type ChatRequest = { messages: ChatTurn[]; preferences: Preferences };
export type ChatResult = { text: string; preferences: Preferences; recommendations: MealRecommendation[]; toolsUsed?: string[] };
