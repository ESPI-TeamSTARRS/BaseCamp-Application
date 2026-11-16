import type { PaymentRequest } from '../lib/order-payment';

interface Props {
  payment: PaymentRequest;
  /** true une fois le client envoyé vers Stripe : le bouton est bloqué (pas de double départ). */
  redirecting: boolean;
  onPay: () => void;
}

/**
 * Mode Stripe : le paiement se fait sur la page sécurisée de Stripe. Cet écran ne demande ni carte ni
 * montant : le total affiché est celui figé côté serveur.
 */
export function StripeCheckoutPanel({ payment, redirecting, onPay }: Props) {
  return (
    <div className="mt-8 space-y-6">
      <dl className="rounded-2xl bg-sand p-5 text-sm">
        <div className="flex justify-between">
          <dt className="text-granite/70">Commande</dt>
          <dd className="font-semibold text-granite">{payment.orderNumber}</dd>
        </div>
        <div className="mt-2 flex justify-between text-base font-bold text-granite">
          <dt>Montant à payer</dt>
          <dd>{payment.amountEur.toFixed(2)} €</dd>
        </div>
      </dl>

      <button
        type="button"
        disabled={redirecting}
        aria-busy={redirecting}
        onClick={onPay}
        className="w-full rounded-full bg-bivouac py-3.5 text-base font-semibold text-white hover:bg-bivouac-dark focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-forest disabled:opacity-60"
      >
        {redirecting ? 'Redirection vers Stripe…' : 'Payer sur la page sécurisée de Stripe'}
      </button>

      <p className="text-sm text-granite/70">
        Vous serez redirigé vers Stripe pour saisir votre carte : cette boutique ne la voit jamais. Mode de
        démonstration : utilisez la carte de test 4242 4242 4242 4242, une date future et un code à trois
        chiffres au choix.
      </p>
    </div>
  );
}
