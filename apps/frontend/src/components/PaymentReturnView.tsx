import Link from 'next/link';
import type { ReturnStatus } from '../lib/payment-return';

export type ReturnView =
  { step: 'checking' } | { step: 'cancelled' } | { step: 'result'; status: ReturnStatus };

interface Props {
  number: string;
  view: ReturnView;
  /** Identifiant de la commande si le navigateur s'en souvient : permet de reprendre le paiement. */
  resumeOrderId: string | null;
  onRetry: () => void;
}

const buttonClass =
  'rounded-full bg-forest px-6 py-3 text-sm font-semibold text-sand hover:bg-forest-light focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bivouac';
const linkClass = 'inline-block text-sm font-medium text-forest underline';

function ResumeLink({ resumeOrderId }: { resumeOrderId: string | null }) {
  if (resumeOrderId === null) {
    return (
      <Link href="/suivi" className={linkClass}>
        Retrouver ma commande
      </Link>
    );
  }
  return (
    <Link href={`/paiement/${resumeOrderId}`} className={buttonClass}>
      Reprendre le paiement
    </Link>
  );
}

/**
 * Retour de Stripe. Aucune information de l'adresse de retour ne dit que la commande est payée : seule
 * la réponse du serveur (webhook signé de Stripe reçu) la fait passer à « payée » (voir PaymentReturn).
 */
export function PaymentReturnView({ number, view, resumeOrderId, onRetry }: Props) {
  if (view.step === 'cancelled') {
    return (
      <div role="status" className="mt-8 space-y-4">
        <p className="font-semibold text-granite">Paiement annulé.</p>
        <p className="text-granite/70">
          Rien n&apos;a été débité. Votre commande {number} est toujours en attente : vous pouvez reprendre le
          paiement quand vous voulez.
        </p>
        <ResumeLink resumeOrderId={resumeOrderId} />
      </div>
    );
  }

  if (view.step === 'checking') {
    return (
      <p role="status" aria-live="polite" className="mt-8 text-granite/70">
        Paiement en cours de confirmation… Ne fermez pas cette page, nous vérifions auprès de Stripe.
      </p>
    );
  }

  const { status } = view;
  if (status.kind === 'paid') {
    return (
      <p role="status" aria-live="polite" className="mt-8 font-semibold text-forest">
        Paiement confirmé. Redirection…
      </p>
    );
  }
  if (status.kind === 'closed') {
    return (
      <div role="alert" className="mt-8 space-y-4">
        <p className="text-granite">
          {status.reason === 'expired'
            ? `La commande ${number} a expiré et ne peut plus être payée.`
            : `La commande ${number} a été remboursée.`}
        </p>
        <Link href="/" className={linkClass}>
          Retour à la boutique
        </Link>
      </div>
    );
  }
  if (status.kind === 'untracked') {
    return (
      <div role="status" className="mt-8 space-y-4">
        <p className="text-granite/70">
          Nous ne pouvons pas suivre cette commande depuis ce navigateur. Votre paiement, s&apos;il a abouti,
          sera confirmé : consultez le suivi avec votre numéro {number} et votre email.
        </p>
        <Link href="/suivi" className={linkClass}>
          Suivi de commande
        </Link>
      </div>
    );
  }
  if (status.kind === 'unavailable') {
    return (
      <div role="alert" className="mt-8 space-y-4">
        <p className="text-red-700">
          Le suivi est momentanément indisponible. Votre paiement n&apos;est pas perdu : réessayez dans un
          instant.
        </p>
        <button type="button" onClick={onRetry} className={buttonClass}>
          Réessayer le suivi
        </button>
      </div>
    );
  }
  return (
    <div role="status" className="mt-8 space-y-4">
      <p className="text-granite">
        La confirmation prend plus de temps que prévu. Votre commande {number} n&apos;est pas encore marquée
        comme payée.
      </p>
      <p className="text-granite/70">
        Si votre carte a bien été acceptée, la confirmation arrive en général sous une minute. Si le paiement
        n&apos;a pas abouti (carte refusée, par exemple), vous pouvez le reprendre : rien n&apos;a été débité.
      </p>
      <div className="flex flex-wrap items-center gap-4">
        <button type="button" onClick={onRetry} className={buttonClass}>
          Vérifier à nouveau
        </button>
        <ResumeLink resumeOrderId={resumeOrderId} />
      </div>
    </div>
  );
}
