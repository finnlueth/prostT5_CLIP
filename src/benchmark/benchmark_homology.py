import pandas as pd
import h5py
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, precision_recall_fscore_support


# Load the cleaned metadata
def load_metadata(metadata_path):
    return pd.read_csv(metadata_path)


# Normalize metadata IDs
def normalize_metadata_ids(metadata):
    return metadata["Domain ID"].str.lower().tolist()


# Extract keys from HDF5 files
def extract_h5_keys(h5_file):
    with h5py.File(h5_file, "r") as f:
        return list(f.keys())


# Refine ProtT5 and CLIPT5 keys
def refine_keys(keys, remove_prefix="cath_current_", split_char=None):
    refined = [key.replace(remove_prefix, "") for key in keys]
    if split_char:
        refined = [key.split(split_char)[0] for key in refined]
    return refined


# Match keys with metadata
def match_keys_with_metadata(refined_keys, metadata_ids):
    metadata_id_set = set(metadata_ids)
    return [key for key in refined_keys if key in metadata_id_set]


# Extract embeddings and labels for ProtT5
def extract_embeddings_and_labels_prott5(h5_file, matching_keys, metadata):
    embeddings = []
    labels = []

    with h5py.File(h5_file, 'r') as f:
        for key in matching_keys:
            hdf5_key = f"cath_current_{key}"
            if hdf5_key in f:
                group = f[hdf5_key]
                dataset_name = list(group.keys())[0]  # Use the first dataset
                embeddings.append(group[dataset_name][:])
                labels.append(
                    metadata.loc[metadata['Domain ID'].str.lower() == key, 'Homologous Superfamily'].values[0])

    if embeddings:
        embeddings = np.vstack(embeddings)
    else:
        embeddings = np.array([])
    return embeddings, labels


# Extract embeddings and labels for CLIPT5
def extract_embeddings_and_labels_clipt5(h5_file, matching_keys, metadata):
    embeddings = []
    labels = []

    with h5py.File(h5_file, 'r') as f:
        for key in matching_keys:
            hdf5_keys = [k for k in f.keys() if key in k]  # Find all keys containing the DomainID
            for hdf5_key in hdf5_keys:
                embeddings.append(f[hdf5_key][:])
                labels.append(
                    metadata.loc[metadata['Domain ID'].str.lower() == key, 'Homologous Superfamily'].values[0])

    if embeddings:
        embeddings = np.vstack(embeddings)
    else:
        embeddings = np.array([])
    return embeddings, labels


# Train and evaluate logistic regression model
def train_and_evaluate(X_train, X_test, y_train, y_test):
    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    balanced_accuracy = balanced_accuracy_score(y_test, y_pred)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, y_pred, average="macro", zero_division=0
    )
    return accuracy, balanced_accuracy, precision, recall, f1


# Synchronize training and test classes
def synchronize_classes(y_train, y_test, X_train, X_test):
    unique_train_classes = set(y_train)
    valid_indices = [i for i, y in enumerate(y_test) if y in unique_train_classes]
    X_test = X_test[valid_indices]
    y_test = [y_test[i] for i in valid_indices]
    return X_train, X_test, y_train, y_test


# Main function for benchmarking
def benchmark(metadata_path, embedding_file_1, embedding_file_2):
    # Load and process metadata
    metadata = load_metadata(metadata_path)
    metadata_ids = normalize_metadata_ids(metadata)

    # Process ProtT5
    keys_1 = extract_h5_keys(embedding_file_1)
    refined_keys_1 = refine_keys(keys_1)
    matching_keys_1 = match_keys_with_metadata(refined_keys_1, metadata_ids)
    embeddings_1, labels_1 = extract_embeddings_and_labels_prott5(embedding_file_1, matching_keys_1, metadata)

    # Verify extracted ProtT5 embeddings
    print(f"Matching keys for ProtT5: {len(matching_keys_1)}")
    print(f"Number of extracted embeddings for ProtT5: {len(embeddings_1)}")

    # Process CLIPT5
    keys_2 = extract_h5_keys(embedding_file_2)
    refined_keys_2 = refine_keys(keys_2, split_char="_")
    matching_keys_2 = match_keys_with_metadata(refined_keys_2, metadata_ids)
    embeddings_2, labels_2 = extract_embeddings_and_labels_clipt5(embedding_file_2, matching_keys_2, metadata)

    # Verify extracted CLIPT5 embeddings
    print(f"Matching keys for CLIPT5: {len(matching_keys_2)}")
    print(f"Number of extracted embeddings for CLIPT5: {len(embeddings_2)}")

    if len(embeddings_2) == 0:
        raise ValueError("No embeddings extracted for CLIPT5. Check the extraction logic and key matching.")

    # Encode labels
    label_encoder = LabelEncoder()
    encoded_labels_1 = label_encoder.fit_transform(labels_1)
    encoded_labels_2 = label_encoder.transform(labels_2)

    # Train-test split
    X_train_1, X_test_1, y_train_1, y_test_1 = train_test_split(embeddings_1, encoded_labels_1, test_size=0.3,
                                                                random_state=42)
    X_train_2, X_test_2, y_train_2, y_test_2 = train_test_split(embeddings_2, encoded_labels_2, test_size=0.3,
                                                                random_state=42)

    # Synchronize classes
    X_train_1, X_test_1, y_train_1, y_test_1 = synchronize_classes(y_train_1, y_test_1, X_train_1, X_test_1)
    X_train_2, X_test_2, y_train_2, y_test_2 = synchronize_classes(y_train_2, y_test_2, X_train_2, X_test_2)

    # Benchmark ProtT5
    accuracy_1, balanced_accuracy_1, precision_1, recall_1, f1_1 = train_and_evaluate(X_train_1, X_test_1, y_train_1,
                                                                                      y_test_1)

    # Benchmark CLIPT5
    accuracy_2, balanced_accuracy_2, precision_2, recall_2, f1_2 = train_and_evaluate(X_train_2, X_test_2, y_train_2,
                                                                                      y_test_2)

    # Prepare results
    results = {
        "Model": ["ProtT5", "CLIPT5"],
        "Top-1 Accuracy": [accuracy_1, accuracy_2],
        "Balanced Accuracy": [balanced_accuracy_1, balanced_accuracy_2],
        "Precision": [precision_1, precision_2],
        "Recall": [recall_1, recall_2],
        "F1-Score": [f1_1, f1_2],
    }

    return pd.DataFrame(results)


# Example usage
metadata_path = "C:/Users/ameli/OneDrive/Dokumente/cleaned_cath_metadata.csv"
embedding_file_1 = "C:/Users/ameli/OneDrive/Dokumente/cath_emb.h5"
embedding_file_2 = "C:/Users/ameli/OneDrive/Dokumente/cath_embeddings_v2.h5"

benchmark_results = benchmark(metadata_path, embedding_file_1, embedding_file_2)
print(benchmark_results)
