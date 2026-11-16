import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { PaymentReturn } from '../../../../components/PaymentReturn';
import { isCancelReturn, isOrderNumber } from '../../../../lib/payment-return';

export const metadata: Metadata = {
  title: 'Retour de paiement',
  robots: { index: false, follow: false },
};

interface Props {
  params: Promise<{ number: string }>;
  searchParams: Promise<{ etat?: string | string[] }>;
}

/**
 * Adresse de retour de Stripe (succès et annulation, `?etat=annule`). Elle ne prouve rien : le statut de
 * la commande est relu côté serveur, jamais déduit de cette adresse.
 */
export default async function PaiementRetourPage({ params, searchParams }: Props) {
  const { number } = await params;
  const { etat } = await searchParams;
  if (!isOrderNumber(number)) notFound();
  return <PaymentReturn number={number} cancelled={isCancelReturn(etat)} />;
}
