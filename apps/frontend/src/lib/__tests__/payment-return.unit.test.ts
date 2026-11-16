import { describe, expect, it, vi } from 'vitest';
import type { TrackedOrder } from '../order-lookup';
import type { RecapResult } from '../order-recap';
import {
  checkOrderStatus,
  followPayment,
  isCancelReturn,
  isOrderNumber,
  recallPaymentOrderId,
  rememberPaymentOrder,
  type ReturnStatus,
} from '../payment-return';

const NUMBER = 'BC-ABCDEFGHJK';
const ORDER_ID = '0b6b1f2e-6c52-4d3b-8a52-1d6f7a9b2c10';

function order(status: string): TrackedOrder {
  return {
    number: NUMBER,
    status,
    statusLabel: status,
    lines: [],
    subtotalEur: 0,
    shippingEur: 0,
    totalEur: 0,
    createdAt: '2026-11-16T10:00:00Z',
  };
}

const recapOf = (status: string) => async (): Promise<RecapResult> => ({
  kind: 'recap',
  order: order(status),
});

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    clear: () => data.clear(),
    key: () => null,
    length: 0,
  } as Storage;
}

describe('isOrderNumber et isCancelReturn', () => {
  it('accepte exactement le format BC-… et rien d’autre', () => {
    expect(isOrderNumber(NUMBER)).toBe(true);
    expect(isOrderNumber('bc-abcdefghjk')).toBe(false);
    expect(isOrderNumber('BC-ABC')).toBe(false);
    expect(isOrderNumber('../x')).toBe(false);
  });

  it('ne reconnaît l’annulation que par la valeur exacte « annule »', () => {
    expect(isCancelReturn('annule')).toBe(true);
    expect(isCancelReturn('succes')).toBe(false);
    expect(isCancelReturn(['annule', 'x'])).toBe(false);
    expect(isCancelReturn(undefined)).toBe(false);
  });
});

describe('mémorisation de la commande à payer', () => {
  it('retrouve l’identifiant de la commande pour son numéro', () => {
    const storage = memoryStorage();
    rememberPaymentOrder(storage, NUMBER, ORDER_ID);

    expect(recallPaymentOrderId(storage, NUMBER)).toBe(ORDER_ID);
  });

  it('refuse un autre numéro, un identifiant qui n’est pas un UUID et un contenu corrompu', () => {
    const storage = memoryStorage();
    rememberPaymentOrder(storage, NUMBER, ORDER_ID);
    expect(recallPaymentOrderId(storage, 'BC-ZZZZZZZZZZ')).toBeNull();

    storage.setItem('basecamp:payment-order', JSON.stringify({ number: NUMBER, orderId: '../admin' }));
    expect(recallPaymentOrderId(storage, NUMBER)).toBeNull();

    storage.setItem('basecamp:payment-order', 'pas du json');
    expect(recallPaymentOrderId(storage, NUMBER)).toBeNull();
    expect(recallPaymentOrderId(undefined, NUMBER)).toBeNull();
  });

  it('survit à un stockage qui refuse l’écriture', () => {
    const refusing = {
      setItem: () => {
        throw new Error('quota');
      },
    } as unknown as Storage;

    expect(() => rememberPaymentOrder(refusing, NUMBER, ORDER_ID)).not.toThrow();
    expect(() => rememberPaymentOrder(undefined, NUMBER, ORDER_ID)).not.toThrow();
  });
});

describe('checkOrderStatus : le statut vient du serveur', () => {
  it('« payée » seulement si le serveur dit payée', async () => {
    expect(await checkOrderStatus(NUMBER, undefined, recapOf('paid'))).toEqual({ kind: 'paid' });
  });

  it('une commande en attente de paiement reste en attente', async () => {
    expect(await checkOrderStatus(NUMBER, undefined, recapOf('pending_payment'))).toEqual({
      kind: 'pending',
    });
  });

  it('un statut inconnu n’est jamais pris pour payé', async () => {
    expect(await checkOrderStatus(NUMBER, undefined, recapOf('quelque_chose'))).toEqual({
      kind: 'pending',
    });
  });

  it.each(['expired', 'refunded'] as const)('une commande %s est close', async (status) => {
    expect(await checkOrderStatus(NUMBER, undefined, recapOf(status))).toEqual({
      kind: 'closed',
      reason: status,
    });
  });

  it('sans email mémorisé, le suivi est impossible', async () => {
    const recap = async (): Promise<RecapResult> => ({ kind: 'no_email' });
    expect(await checkOrderStatus(NUMBER, undefined, recap)).toEqual({ kind: 'untracked' });
  });

  it('un serveur muet ou une exception donnent « indisponible », jamais « payée »', async () => {
    const down = async (): Promise<RecapResult> => ({ kind: 'unavailable' });
    const boom = async (): Promise<RecapResult> => {
      throw new Error('réseau');
    };
    expect(await checkOrderStatus(NUMBER, undefined, down)).toEqual({ kind: 'unavailable' });
    expect(await checkOrderStatus(NUMBER, undefined, boom)).toEqual({ kind: 'unavailable' });
  });
});

describe('followPayment', () => {
  const noWait = async () => {};

  it('s’arrête dès que le serveur confirme le paiement', async () => {
    const check = vi
      .fn<(n: string, s: Storage | undefined) => Promise<ReturnStatus>>()
      .mockResolvedValueOnce({ kind: 'pending' })
      .mockResolvedValueOnce({ kind: 'pending' })
      .mockResolvedValueOnce({ kind: 'paid' });

    const result = await followPayment(NUMBER, undefined, { check, sleep: noWait });

    expect(result).toEqual({ kind: 'paid' });
    expect(check).toHaveBeenCalledTimes(3);
  });

  it('rend « en attente » quand le webhook tarde au-delà des tentatives', async () => {
    const check = vi.fn().mockResolvedValue({ kind: 'pending' });
    const sleep = vi.fn(noWait);

    const result = await followPayment(NUMBER, undefined, { check, sleep, attempts: 4, delayMs: 10 });

    expect(result).toEqual({ kind: 'pending' });
    expect(check).toHaveBeenCalledTimes(4);
    expect(sleep).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledWith(10);
  });

  it('rend « indisponible » si le serveur ne répond pas jusqu’au bout', async () => {
    const check = vi.fn().mockResolvedValue({ kind: 'unavailable' });

    expect(await followPayment(NUMBER, undefined, { check, sleep: noWait, attempts: 3 })).toEqual({
      kind: 'unavailable',
    });
  });

  it('reprend après une panne passagère', async () => {
    const check = vi
      .fn<(n: string, s: Storage | undefined) => Promise<ReturnStatus>>()
      .mockResolvedValueOnce({ kind: 'unavailable' })
      .mockResolvedValueOnce({ kind: 'paid' });

    expect(await followPayment(NUMBER, undefined, { check, sleep: noWait })).toEqual({ kind: 'paid' });
  });

  it.each([{ kind: 'closed', reason: 'expired' }, { kind: 'untracked' }] as ReturnStatus[])(
    's’arrête tout de suite sur une réponse définitive %j',
    async (final) => {
      const check = vi.fn().mockResolvedValue(final);

      expect(await followPayment(NUMBER, undefined, { check, sleep: noWait })).toEqual(final);
      expect(check).toHaveBeenCalledTimes(1);
    }
  );

  it('cesse de suivre quand l’écran est quitté', async () => {
    const check = vi.fn().mockResolvedValue({ kind: 'pending' });
    let alive = true;
    const sleep = async () => {
      alive = false;
    };

    await followPayment(NUMBER, undefined, { check, sleep, shouldContinue: () => alive });

    expect(check).toHaveBeenCalledTimes(1);
  });
});
