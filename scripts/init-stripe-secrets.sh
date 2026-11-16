#!/bin/sh
# Enregistre une cle Stripe de TEST dans un fichier de secret (jamais dans le depot, jamais dans
# l'historique du shell, jamais affichee) :
#   sh scripts/init-stripe-secrets.sh             # cle secrete  -> <dossier>/stripe-secret-key
#   sh scripts/init-stripe-secrets.sh --webhook   # secret de webhook (whsec_) -> <dossier>/stripe-webhook-secret
#   --force : remplace un fichier existant (sinon refuse, pour ne jamais perdre une cle par erreur)
# Le dossier est STRIPE_SECRETS_DIR (defaut : .secrets/payment, ignore par Git). Sur la VM :
#   sudo -u basecamp STRIPE_SECRETS_DIR=/opt/basecamp/secrets/payment sh scripts/init-stripe-secrets.sh
# La valeur est lue sur l'entree standard, SANS ECHO si c'est un terminal : coller la cle puis Entree.
# Seule une cle secrete `sk_test_...` est acceptee (mode test) : une cle live est refusee.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${STRIPE_SECRETS_DIR:-$ROOT/.secrets/payment}"
KIND="key"
FORCE=0
for argument in "$@"; do
  case "$argument" in
    --webhook) KIND="webhook" ;;
    --force) FORCE=1 ;;
    *) echo "Argument inconnu : $argument" >&2; exit 2 ;;
  esac
done

if [ "$KIND" = "webhook" ]; then
  FILE="$DIR/stripe-webhook-secret"
  LABEL="le secret de signature du webhook (whsec_...)"
else
  FILE="$DIR/stripe-secret-key"
  LABEL="la cle secrete de test (sk_test_...)"
fi

if [ -e "$FILE" ] && [ "$FORCE" != 1 ]; then
  echo "Le fichier existe deja ($FILE) : relancer avec --force pour le remplacer." >&2
  exit 1
fi

# Le dossier doit etre accessible en ecriture AVANT de demander la cle : sinon la saisie serait perdue.
if ! mkdir -p "$DIR" 2>/dev/null || [ ! -d "$DIR" ] || [ ! -w "$DIR" ]; then
  echo "Le dossier $DIR n'est pas accessible en ecriture pour $(id -un)." >&2
  echo "Le creer ou le reparer avec le bon proprietaire (puis relancer) :" >&2
  echo "  sudo install -d -o $(id -un) -g $(id -gn) -m 750 $DIR" >&2
  exit 1
fi

if [ -t 0 ]; then
  printf 'Collez %s puis Entree (rien ne s affiche) : ' "$LABEL" >&2
  stty -echo
  trap 'stty echo' EXIT INT TERM
  IFS= read -r VALUE || VALUE=""
  stty echo
  printf '\n' >&2
else
  IFS= read -r VALUE || VALUE=""
fi
VALUE="$(printf '%s' "$VALUE" | tr -d '[:space:]')"

if [ "$KIND" = "webhook" ]; then
  printf '%s' "$VALUE" | grep -Eq '^whsec_[A-Za-z0-9]{16,}$' \
    || { echo "Refuse : un secret de webhook commence par whsec_." >&2; exit 1; }
else
  printf '%s' "$VALUE" | grep -Eq '^sk_test_[A-Za-z0-9]{16,}$' \
    || { echo "Refuse : seule une cle secrete de test (sk_test_...) est acceptee." >&2; exit 1; }
fi

mkdir -p "$DIR"
( umask 077; printf '%s\n' "$VALUE" > "$FILE" )
# Lisible par l'utilisateur non privilegie du conteneur (le dossier parent reste ferme aux autres).
chmod 644 "$FILE"
VALUE=""
echo "Enregistre dans $FILE (la valeur n'est pas affichee)."
