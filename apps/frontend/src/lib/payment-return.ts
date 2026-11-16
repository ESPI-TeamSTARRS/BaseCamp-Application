import { loadRecap, type RecapResult } from './order-recap';

const STORAGE_KEY = 'basecamp:payment-order';
const ORDER_NUMBER = /^BC-[A-Z0-9]{10}$/;
const ORDER_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const POLL_ATTEMPTS = 12;
export const POLL_DELAY_MS = 2500;

/**
 * Où en est la commande, vue du SERVEUR uniquement : jamais déduit de l'adresse de retour de Stripe.
 * - paid : le webhook signé a été reçu, la commande est payée;
 * - closed : la commande ne peut plus être payée (expirée ou remboursée);
 * - pending : toujours en attente de confirmation (webhook lent, ou paiement non abouti);
 * - untracked : le suivi est impossible depuis ce navigateur (email non mémorisé);
 * - unavailable : le serveur ne répond pas.
 */
export type ReturnStatus =
  | { kind: 'paid' }
  | { kind: 'closed'; reason: 'expired' | 'refunded' }
  | { kind: 'pending' }
  | { kind: 'untracked' }
  | { kind: 'unavailable' };

type Recap = (number: string, storage: Storage | undefined) => Promise<RecapResult>;

export function isOrderNumber(value: string): boolean {
  return ORDER_NUMBER.test(value);
}

/** L'adresse de retour d'annulation porte `?etat=annule` : seule cette valeur exacte compte. */
export function isCancelReturn(state: string | string[] | undefined): boolean {
  return state === 'annule';
}

/**
 * Avant de partir chez Stripe, on retient quelle commande (identifiant) correspond à ce numéro, dans le
 * stockage de l'onglet : au retour, le client peut reprendre le paiement. Un stockage refusé ne casse rien.
 */
export function rememberPaymentOrder(storage: Storage | undefined, number: string, orderId: string): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify({ number, orderId }));
  } catch {
    // Stockage indisponible : le retour proposera le suivi de commande à la place.
  }
}

export function recallPaymentOrderId(storage: Storage | undefined, number: string): string | null {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as { number?: unknown; orderId?: unknown };
    const valid = saved.number === number && typeof saved.orderId === 'string';
    return valid && ORDER_ID.test(saved.orderId as string) ? (saved.orderId as string) : null;
  } catch {
    return null;
  }
}

/** Une lecture du statut de la commande. Ne rejette jamais. */
export async function checkOrderStatus(
  number: string,
  storage: Storage | undefined,
  recap: Recap = loadRecap
): Promise<ReturnStatus> {
  let result: RecapResult;
  try {
    result = await recap(number, storage);
  } catch {
    return { kind: 'unavailable' };
  }
  if (result.kind === 'no_email') return { kind: 'untracked' };
  if (result.kind === 'unavailable') return { kind: 'unavailable' };
  const status = result.order.status;
  if (status === 'paid') return { kind: 'paid' };
  if (status === 'expired' || status === 'refunded') return { kind: 'closed', reason: status };
  return { kind: 'pending' };
}

interface FollowOptions {
  attempts?: number;
  delayMs?: number;
  sleep?: (ms: number) => Promise<void>;
  check?: (number: string, storage: Storage | undefined) => Promise<ReturnStatus>;
  /** Appelé avant chaque nouvelle tentative (ex. pour annuler le suivi quand l'écran est quitté). */
  shouldContinue?: () => boolean;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Suit la commande jusqu'à une réponse définitive du serveur, ou jusqu'à épuisement des tentatives :
 * le résultat reste alors `pending` (ou `unavailable` si le serveur ne répondait pas), et l'écran propose
 * de réessayer le suivi. « paid » n'est jamais renvoyé sans que le serveur l'ait dit.
 */
export async function followPayment(
  number: string,
  storage: Storage | undefined,
  options: FollowOptions = {}
): Promise<ReturnStatus> {
  const {
    attempts = POLL_ATTEMPTS,
    delayMs = POLL_DELAY_MS,
    sleep = wait,
    check = checkOrderStatus,
    shouldContinue = () => true,
  } = options;
  let last: ReturnStatus = { kind: 'pending' };
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (!shouldContinue()) return last;
    last = await check(number, storage);
    if (last.kind === 'paid' || last.kind === 'closed' || last.kind === 'untracked') return last;
    if (attempt < attempts - 1) await sleep(delayMs);
  }
  return last;
}
