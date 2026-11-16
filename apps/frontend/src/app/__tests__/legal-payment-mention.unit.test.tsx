import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { legalUpdatedAt } from '../../lib/legal-info';
import CgvPage from '../cgv/page';
import ConfidentialitePage from '../confidentialite/page';

const text = (html: string) =>
  html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ');

describe('CGV : paiement', () => {
  const page = text(renderToStaticMarkup(<CgvPage />));

  it('annonce un paiement de démonstration en mode test Stripe, sans carte réelle', () => {
    expect(page).toContain('paiement de démonstration en mode test Stripe');
    expect(page).toContain('aucune carte réelle');
    expect(page).toContain('aucun débit réel');
  });

  it('donne la carte de test et précise que le vendeur ne voit pas la carte', () => {
    expect(page).toContain('4242 4242 4242 4242');
    expect(page).toContain('ne voit ni ne conserve les données de carte');
  });

  it('ne dit plus que le paiement est simulé', () => {
    expect(page).not.toMatch(/paiement est simulé/);
    expect(page).not.toMatch(/service de paiement est simulé/);
  });
});

describe('Politique de confidentialité : paiement', () => {
  const page = text(renderToStaticMarkup(<ConfidentialitePage />));

  it('annonce un paiement de démonstration en mode test Stripe, aucune carte réelle', () => {
    expect(page).toContain('paiement de démonstration en mode test Stripe, aucune carte réelle');
  });

  it('dit que les données de carte ne sont jamais reçues et nomme Stripe parmi les sous-traitants', () => {
    expect(page).toContain('jamais reçues ni conservées');
    expect(page).toContain('par Stripe en mode test');
  });

  it('ne dit plus que le paiement est simulé', () => {
    expect(page).not.toMatch(/le paiement est simulé/);
  });
});

describe('Date de mise à jour des textes légaux', () => {
  it('reflète la mise à jour du 17 novembre 2026', () => {
    expect(legalUpdatedAt).toBe('17 novembre 2026');
  });
});
