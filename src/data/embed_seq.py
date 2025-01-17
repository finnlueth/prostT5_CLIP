import logging
from pathlib import Path
from typing import List, Tuple, Union

import h5py
import numpy as np
import pandas as pd
import requests
import torch
from pyfaidx import Fasta
from tqdm import tqdm
from transformers import AutoTokenizer, EsmModel, T5EncoderModel, T5Tokenizer

from src.utils.config import get_params


def setup_model(checkpoint, device: torch.device = "mps") -> Tuple:
    try:
        if "esm" in checkpoint:
            mod_type = "esm"
            tokenizer = AutoTokenizer.from_pretrained(checkpoint)
            model = EsmModel.from_pretrained(checkpoint)
            model = model.to(device)
        elif "ankh" in checkpoint:
            mod_type = "ankh"
            tokenizer = AutoTokenizer.from_pretrained(checkpoint)
            model = T5EncoderModel.from_pretrained(checkpoint)
            model = model.to(device)
        else:
            mod_type = "pt"
            tokenizer = T5Tokenizer.from_pretrained(checkpoint, legacy=False)
            model = T5EncoderModel.from_pretrained(checkpoint, torch_dtype=torch.float16)
            model = model.to(device)
            model = model.half()

        return model, tokenizer, mod_type
    except Exception as e:
        logging.error(f"Error loading model: {e}")
        raise


def seq_preprocess(df, model_type="esm") -> Union[pd.DataFrame, None]:
    df["sequence"] = df["sequence"].str.replace("[UZO]", "X", regex=True)

    if model_type in "esm" or model_type == "ankh":
        return df
    elif model_type == "pt":
        df["sequence"] = df.apply(lambda row: " ".join(row["sequence"]), axis=1)
        return df
    else:
        return None


def read_fasta(file_path: Path) -> Tuple[list, list]:
    headers = []
    sequences = []
    fasta = Fasta(str(file_path))
    for seq in fasta:
        headers.append(seq.name)
        sequences.append(str(seq))
    return headers, sequences


def create_embedding(
    checkpoint: str,
    df: pd.DataFrame,
    emb_type="per_prot",
    max_seq_len=1024,
    device: torch.device = "mps",
    outputs: Path = Path("embeddings/embeddings.h5"),
):
    model, tokenizer, mod_type = setup_model(checkpoint, device=device)
    model.eval()
    preprocessed = seq_preprocess(df, mod_type)

    def compute_embedding(
        sequence: Union[str, List[str]], emb_type: str, max_seq_len: int = 1024, model=model, tokenizer=tokenizer
    ):
        inputs = tokenizer(
            sequence,
            return_tensors="pt",
            max_length=max_seq_len,
            truncation=True,
            padding=True,
            add_special_tokens=True,
        ).to(device)
        with torch.no_grad():
            outputs = model(**inputs).last_hidden_state.cpu()

            # Add validation
            if not torch.isfinite(outputs).all():
                non_finite = (~torch.isfinite(outputs)).sum().item()
                logging.warning(f"Found {non_finite} non-finite values in sequence embedding")
                outputs = torch.nan_to_num(outputs, nan=0.0, posinf=0.0, neginf=0.0)

            outputs = outputs.numpy()

        if emb_type == "per_res":
            # remove special tokens
            if mod_type in ["pt", "ankh"]:
                outputs = outputs[:-1, :]
            elif mod_type == "esm":
                outputs = np.squeeze(outputs, axis=0)[:-1, :]
            return outputs
        elif emb_type == "per_prot":
            return outputs.mean(axis=1).flatten()
        else:
            raise ValueError("Input valid embedding type")

    steps = 1000

    with h5py.File(outputs, "a") as hdf:
        embs, headers = [], []

        for i, (_, row) in enumerate(
            tqdm(preprocessed.iterrows(), total=len(preprocessed), desc="Embedding sequences", leave=False)
        ):
            sequence = row["sequence"]
            header = row["header"]

            if header in hdf:
                tqdm.write(f"Protein {header} already in sequence embeddings, skipping")
                continue

            embedding = compute_embedding(sequence, emb_type, max_seq_len=max_seq_len)
            embs.append(embedding)
            headers.append(header)

            # Save every 1000 sequences
            if (i + 1) % steps == 0 or i == len(preprocessed) - 1:
                for h, emb in zip(headers, embs):
                    if h not in hdf:
                        hdf.create_dataset(name=h, data=emb)
                tqdm.write(f"Saved {len(headers)} embeddings to {outputs}")
                embs, headers = [], []
                hdf.flush()  # Force write to disk

    logging.info(f"Embeddings saved to {outputs}")

    del model
    del tokenizer
    del df
    torch.cuda.empty_cache()


def split_embeddings(fasta, embs, dataset: Path, split: str) -> dict:
    """
    Splits embeddings from a given FASTA file and stores them in an HDF5 dataset.

    Args:
        fasta (str): Path to the FASTA file containing sequences.
        embs (dict): HDF5 dict of uniprot protein embeddings.
        dataset (Path): Path to the HDF5 file where embeddings will be stored.
        split (str): Name of the split (e.g. train_set, test_set).

    Returns:
        dict: A dictionary containing sequences for which embeddings were not found.
    """
    headers, sequences = read_fasta(fasta)
    seqs_to_embed = {"header": [], "sequence": []}

    with h5py.File(dataset, "a") as hdf:
        for header, sequence in tqdm(
            zip(headers, sequences),
            total=len(headers),
            desc=f"splitting embeddings for {split}",
            leave=False,
        ):
            if header in hdf:
                tqdm.write(f"Protein {header} already in {split}, skipping.")
                continue

            if header not in embs:
                seqs_to_embed["header"].append(header)
                seqs_to_embed["sequence"].append(sequence)
                continue

            else:
                hdf.create_dataset(name=header, data=embs[header][()])

    tqdm.write(f"Found {len(headers) - len(seqs_to_embed['header'])} embeddings for {split} in uniprot")

    return seqs_to_embed


def main():
    params = get_params("embed_seq")

    uniprot = Path(params["uniprot"]).resolve()
    if not uniprot.exists():
        logging.info("Downloading UniProt embeddings...")
        response = requests.get(
            params["url"],
            stream=True,
        )
        with open(uniprot, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    with h5py.File(Path(params["uniprot"]).resolve(), "r") as emb:
        for split in ["train_set", "test_set"]:
            inputs = Path(params[split]["sequence"]).resolve()

            create_embedding(
                checkpoint=params["model"],
                df=pd.DataFrame(split_embeddings(inputs, emb, Path(params["output"]), split)),
                emb_type=params["emb_type"],
                max_seq_len=params["max_seq_len"],
                device=torch.device(params["device"]),
                outputs=Path(params["output"]),
            )


if __name__ == "__main__":
    main()
