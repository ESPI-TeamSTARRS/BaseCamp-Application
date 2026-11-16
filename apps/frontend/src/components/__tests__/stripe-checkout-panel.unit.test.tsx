import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { PaymentRequest } from '../../lib/order-payment';
import { StripeCheckoutPanel } from '../StripeCheckoutPanel';

const payment: PaymentRequest = {
  intentId: 'pi-1',
  orderNumber: 'BC-ABCDEFGHJK',
  amountEur: 44.9,
  methods: [],
  checkoutUrl: 'https://checkout.stripe.com/c/pay/cs_test_a1b2c3',
};

function render(redirecting: boolean) {
  return renderToStaticMarkup(
    <StripeCheckoutPanel payment={payment} redirecting={redirecting} onPay={() => {}} />
  );
}

describe('StripeCheckoutPanel', () => {
  it('affiche la commande et le total figé côté serveur', () => {
    const html = render(false);

    expect(html).toContain('BC-ABCDEFGHJK');
    expect(html).toContain('44.90 €');
  });

  it('propose un seul bouton vers Stripe et aucun bouton de test ni champ de carte', () => {
    const html = render(false);

    expect(html).toContain('Payer sur la page sécurisée de Stripe');
    expect(html).not.toContain('Simuler');
    expect(html).not.toContain('<input');
  });

  it('n’expose jamais l’adresse de Stripe dans le balisage (la redirection est un geste du client)', () => {
    expect(render(false)).not.toContain('checkout.stripe.com');
  });

  it('indique la carte de test de démonstration', () => {
    expect(render(false)).toContain('4242 4242 4242 4242');
  });

  it('bloque le bouton pendant la redirection pour éviter un double départ', () => {
    const html = render(true);

    expect(html).toContain('disabled');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('Redirection vers Stripe');
  });
});
