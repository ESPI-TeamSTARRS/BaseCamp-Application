import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ReturnStatus } from '../../lib/payment-return';
import { PaymentReturnView, type ReturnView } from '../PaymentReturnView';

const NUMBER = 'BC-ABCDEFGHJK';
const ORDER_ID = '0b6b1f2e-6c52-4d3b-8a52-1d6f7a9b2c10';

function render(view: ReturnView, resumeOrderId: string | null = ORDER_ID) {
  return renderToStaticMarkup(
    <PaymentReturnView number={NUMBER} view={view} resumeOrderId={resumeOrderId} onRetry={() => {}} />
  );
}

const result = (status: ReturnStatus): ReturnView => ({ step: 'result', status });

describe('PaymentReturnView : annulation', () => {
  const html = render({ step: 'cancelled' });

  it('dit clairement que rien n’a été débité et que la commande reste payable', () => {
    expect(html).toContain('Paiement annulé.');
    expect(html).toContain('Rien n&#x27;a été débité');
    expect(html).toContain(NUMBER);
  });

  it('propose de reprendre le paiement de la bonne commande', () => {
    expect(html).toContain(`href="/paiement/${ORDER_ID}"`);
    expect(html).toContain('Reprendre le paiement');
  });

  it('renvoie vers le suivi si la commande n’est pas mémorisée', () => {
    const unknown = render({ step: 'cancelled' }, null);

    expect(unknown).toContain('href="/suivi"');
    expect(unknown).not.toContain('Reprendre le paiement');
  });

  it('est annoncée aux lecteurs d’écran', () => {
    expect(html).toContain('role="status"');
  });
});

describe('PaymentReturnView : attente de la confirmation', () => {
  it('affiche « paiement en cours de confirmation », annoncé poliment, sans jamais dire « payée »', () => {
    const html = render({ step: 'checking' });

    expect(html).toContain('Paiement en cours de confirmation');
    expect(html).toContain('aria-live="polite"');
    expect(html).not.toContain('Paiement confirmé');
  });

  it('un webhook lent laisse « en attente », avec un nouvel essai et la reprise du paiement', () => {
    const html = render(result({ kind: 'pending' }));

    expect(html).toContain('n&#x27;est pas encore marquée comme payée');
    expect(html).toContain('Vérifier à nouveau');
    expect(html).toContain(`href="/paiement/${ORDER_ID}"`);
    expect(html).not.toContain('Paiement confirmé');
  });

  it('« paiement confirmé » seulement quand le serveur a confirmé', () => {
    expect(render(result({ kind: 'paid' }))).toContain('Paiement confirmé');
  });
});

describe('PaymentReturnView : autres issues', () => {
  it.each([
    ['expired', 'a expiré et ne peut plus être payée'],
    ['refunded', 'a été remboursée'],
  ] as const)('commande %s : message clair et retour à la boutique', (reason, text) => {
    const html = render(result({ kind: 'closed', reason }));

    expect(html).toContain(text);
    expect(html).toContain('href="/"');
    expect(html).toContain('role="alert"');
  });

  it('suivi impossible : renvoie vers le suivi de commande sans rien affirmer', () => {
    const html = render(result({ kind: 'untracked' }));

    expect(html).toContain('href="/suivi"');
    expect(html).not.toContain('Paiement confirmé');
  });

  it('serveur indisponible : message assaini et nouvel essai du suivi', () => {
    const html = render(result({ kind: 'unavailable' }));

    expect(html).toContain('momentanément indisponible');
    expect(html).toContain('Réessayer le suivi');
    expect(html).toContain('role="alert"');
  });
});
