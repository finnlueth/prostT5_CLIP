# prostT5 + CLIP
Combining Protein and LLM Embeddings with CLIP for Protein Design and Function Prediction

Protein design and function prediction are crucial tasks in computational biology with significant
implications for drug discovery, enzyme engineering, and protein understanding. Recent advance-
ments in protein language models (pLMs) and large language models (LLMs) have shown promise
in capturing intricate patterns in protein sequences and their associated textual descriptions.
This project explores the potential of combining protein embeddings from pLMs (specifically
ProtT5) with text embeddings from LLMs (using Microsoft’s Phi-3.5 model) in a unified framework
inspired by CLIP (Contrastive Language-Image Pre-training). While CLIP was originally designed
to align image and text embeddings, we propose adapting this approach to create a shared
embedding space for protein sequences and their textual descriptions.
The ultimate goals of this project are twofold: 1. Protein Design: Infer protein sequences from
textual descriptions of their properties or functions. 2. Function Prediction: Predict protein
descriptions from a given sequences.
By leveraging the strengths of both protein and language models, we aim to develop a powerful
tool for bidirectional protein-text understanding and generation.

## DVC setup
1. Install DVC: `pip install dvc`
2. Initialize DVC: `dvc init`
3. Add data: `dvc add data/`
4. Commit changes: `git add data.dvc data/.gitignore && git commit -m "Add data"`
5. Push to remote: `git push`
6. Run pipeline: `dvc repro`

## Folder Structure
prostT5-CLIP/
├── .dvc/
├── .git/
├── cafa5/
├── outputs/
├── embeddings/
├── src/
│   └── data/
│       └── cluster.py
│       └── dataset.py
│   └── model/
├── .dvcignore
├── .gitignore
├── dvc.yaml
└── params.yaml