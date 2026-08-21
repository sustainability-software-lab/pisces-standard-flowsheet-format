<!-- AUTO-GENERATED FILE -- DO NOT EDIT BY HAND.
     Generated from pisces_sff/models/all_models.yaml by pisces_sff/_registry.py.
     Regenerate with: python -m pisces_sff._registry
     (or activate the committed pre-commit hook -- see below). -->

# SFF model recipes and exported flowsheets

This file indexes the model recipes under `pisces_sff/models/` and the
reference SFF exports under `pisces_sff/exported_flowsheets/`. It is generated
from `pisces_sff/models/all_models.yaml` -- the single source of truth for
model <-> flowsheet pairing. Edit that file, not this one. Only items with
both a model recipe and an exported flowsheet are registered.

## Naming convention

- Exported flowsheets are named `SF_<SIMULATOR>_<NN>` (`SF` = standard
  flowsheet); model recipes are named `M_<SIMULATOR>_<NN>` (`M` = model).
  `BST` = BioSTEAM; future simulators get their own uppercase code.
- Numbers are opaque, permanent IDs assigned in registration order: a new
  item takes the next free number when added; numbers are never reused and
  never re-sorted to restore any ordering property. They are zero-padded to
  two digits, and to three digits from 100 on (`SF_BST_100`). IDs are
  identifiers, not sort keys.
- A paired model and flowsheet usually share a number (`M_BST_01` <->
  `SF_BST_01`), but the authoritative pairing is the registry entry in
  `all_models.yaml`, not the string convention. Code must resolve pairing
  through the registry only.
- Items were renamed from earlier descriptive filenames with `git mv`; trace
  any file's history across the rename with `git log --follow <path>`.

## Keeping this file in sync

A committed pre-commit hook regenerates this README on every commit. Activate
it once per clone:

    git config core.hooksPath .githooks
    git config sff.python <path-to-a-python-with-pyyaml>   # only if python3/python don't resolve

## Registered models

| Model ID | Flowsheet ID | Title | Description | Simulator | Source corpus |
| --- | --- | --- | --- | --- | --- |
| M_BST_01 | SF_BST_01 | Corn dry-grind ethanol | Dry-grind corn-to-ethanol biorefinery from the Bioindustrial-Park corn biorefinery (BioSTEAM). | biosteam | Bioindustrial-Park |
| M_BST_02 | SF_BST_02 | Corn to 3-hydroxypropionic acid to acrylic acid | Corn-fed 3-hydroxypropionic acid (Issatchenkia orientalis, low-pH fermentation) catalytically upgraded to acrylic acid, from the Bioindustrial-Park HP biorefinery (BioSTEAM, HP_2025_no_FGI branch); the corn scenario of Tan et al., Nat. Commun. 2026. | biosteam | Bioindustrial-Park |
