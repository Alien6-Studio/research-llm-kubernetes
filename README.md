# Débit utile des applications Kubernetes appelant un LLM

Recherche associée à l’article Alien6 conservé à l’URL
`/fr/research/articles/mise-en-production-ia-kubernetes/`.

**Question :** comment le nombre de réplicas, le quota partagé du service LLM et
la régulation des appels influencent-ils le débit réellement soutenable ?

## État du travail

[Le protocole](PROTOCOL.md) est préparé avant les mesures. Un cluster kind local
dédié `a6-research` est disponible, limité à 4 CPU et 8 Gio pour son nœud. Le banc
de charge reste à implémenter. **Aucun résultat expérimental n’est annoncé.**

Le projet conserve son propre kubeconfig, exclu de Git. Il ne déploie pas dans
les clusters applicatifs existants et ne change pas leur contexte courant.

## Campagnes prévues

1. API cliente et serveur d’inférence émulé : quotas et temps de réponse maîtrisés,
   1, 2, 4 et 8 réplicas, charge régulière ou irrégulière, essais répétés.
2. Service Ollama réel : vérifier quels comportements sont transférables à une
   génération effective. Les mesures restent distinctes de celles de l’émulateur.

Chaque campagne conservera les traces de charge, les résultats bruts, les
latences complètes, les refus, les files d’attente et les ressources consommées.
Un coût financier sera identifié comme estimation si seules des hypothèses de
prix sont disponibles. Les ressources par requête seront mesurées séparément.

## Organisation

- `PROTOCOL.md` : hypothèses et contrôles.
- `infra/` : environnement de mesure reproductible.
- `runs/` : paramètres et mesures brutes des campagnes.

Le dépôt est privé pendant la préparation de la recherche. Aucun livrable client
ni élément d’infrastructure privée n’y est publié.
