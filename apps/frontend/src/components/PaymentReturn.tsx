'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { useCart } from '../context/CartContext';
import { followPayment, recallPaymentOrderId } from '../lib/payment-return';
import { PaymentReturnView, type ReturnView } from './PaymentReturnView';

interface Props {
  number: string;
  cancelled: boolean;
}

/** Retour de Stripe : annulation, ou suivi du statut serveur jusqu'à la confirmation du webhook. */
export function PaymentReturn({ number, cancelled }: Props) {
  const router = useRouter();
  const { clearCart } = useCart();
  const [view, setView] = useState<ReturnView>(cancelled ? { step: 'cancelled' } : { step: 'checking' });
  const [resumeOrderId, setResumeOrderId] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    // Lecture du stockage de l'onglet (absent côté serveur) : mise à jour dans un rappel asynchrone.
    void Promise.resolve(recallPaymentOrderId(window.sessionStorage, number)).then(setResumeOrderId);
  }, [number]);

  useEffect(() => {
    if (cancelled) return;
    let alive = true;
    void followPayment(number, window.sessionStorage, { shouldContinue: () => alive }).then((status) => {
      if (!alive) return;
      if (status.kind === 'paid') {
        // Statut confirmé par le serveur : seulement maintenant le panier se vide et le succès s'affiche.
        clearCart();
        router.push(`/success?commande=${encodeURIComponent(number)}`);
      }
      setView({ step: 'result', status });
    });
    return () => {
      alive = false;
    };
  }, [number, cancelled, attempt, clearCart, router]);

  return (
    <section className="mx-auto max-w-lg px-4 py-12">
      <h1 className="flex items-center gap-2 text-2xl font-bold text-granite">
        <ShieldCheck className="text-forest" /> Paiement
      </h1>
      <PaymentReturnView
        number={number}
        view={view}
        resumeOrderId={resumeOrderId}
        onRetry={() => {
          setView({ step: 'checking' });
          setAttempt((n) => n + 1);
        }}
      />
    </section>
  );
}
