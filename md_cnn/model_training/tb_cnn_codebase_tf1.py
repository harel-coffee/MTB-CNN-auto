"""
Code for running CNN on MTB data to predict ABR phenotypes
Authors:
	Michael Chen (original version)
	Anna G. Green
	Chang-ho Yoon
"""


import sys
import glob
import os
import yaml
import sparse

import numpy as np
import pandas as pd
import tensorflow.keras.backend as K
import tensorflow as tf

from Bio import SeqIO

# Mapping to use for one-hot encoding
BASE_TO_COLUMN = {'A': 0, 'C': 1, 'T': 2, 'G': 3, '-': 4}

# Get one hot vector
def get_one_hot(sequence):
    """
	Creates a one-hot encoding of a sequence
	Parameters
	----------
	sequence: iterable of str
		Sequence containing only ACTG- characters

	Returns
	-------
	np.ndarray of int
		L (seq len) x 5 one-hot encoded sequence
	"""

    seq_len = len(sequence)
    seq_in_index = [BASE_TO_COLUMN.get(b, b) for b in sequence]
    one_hot = np.zeros((seq_len, 5))

    # Assign the found positions to 1
    one_hot[np.arange(seq_len), np.array(seq_in_index)] = 1

    return one_hot

def sequence_dictionary(filename):
    """
	Creates a dataframe that contains the sequence of each locus for each isolate
	Note that this function splits the identifier names in the fasta file on '/'
	and takes the last entry

	Parameters
	----------
	filename: str
		path to directory containing genotype data (one fasta file containing
		sequences for all isolates at a particular locus)

	Returns
	-------
	pd.DataFrame with one column, indexed by strain name
		column name will be the beginning string of the file name
	"""
    seq_dict = SeqIO.to_dict(
        SeqIO.parse(filename, "fasta"),
        key_function=lambda x: x.id.split("/")[-1].split(".cut")[0])

    # create a dictionary of identifier: sequence
    for identifier, sequence in seq_dict.items():
        seq_dict[str(identifier)] = str(sequence.seq)

    df = pd.DataFrame.from_dict(seq_dict, orient='index')
    gene_name = filename.split("/")[-1].split("_")[0]
    df.columns = [gene_name]

    return df


def make_genotype_df(genotype_input_directory):
    """
    Create a dataframe with the genotypes for each isolate at each locus
    Hard-codes the ordering of the loci so that they are in same order upon re-runs

	Parameters
	----------
	genotype_input_directory: str
		path to directory containing fasta files of genotype inputs

	Returns
	-------
	pd.DataFrame:
		indexed by isolate name, one column per locus
	"""
    # Make a df that combines all genotype data
    dfs_list = []

    locus_order = [
        "acpM-kasA_20201206",
        "gid_20201206",
        "rpsA_20201206",
        "clpC_20201213",
        "embCAB_20201206",
        "aftB-ubiA_20201206",
        "rrs-rrl_20201206",
        "ethAR_20201206",
        "oxyR-ahpC_20201206",
        "tlyA_20201206",
        "KatG_20201206",
        "rpsL_20201206",
        "rpoBC_20201206",
        "FabG1-inhA_20201206",
        "eis_20201206",
        "gyrBA_20201206",
        "panD_20201213",
        "pncA_20201206"
    ]

    for l in locus_order:
        df_file = f"{genotype_input_directory}/{l}.fasta"
        print("reading fasta file", df_file)
        _df = sequence_dictionary(df_file)
        print("found this many seqs", len(_df))
        dfs_list.append(_df)
    df_genos = dfs_list[0].join(dfs_list[1:], how='outer')
    print("size of df geno", len(df_genos))
    return df_genos


# Change phenotype data to 0s and 1s
def rs_encoding_to_numeric(df_geno_pheno, drugs_list):
    """
	Creates a matrix of y values (resistance/sensitivity)
	to each drug, encoded as 0's and 1's

    Note: encodes sensitivity as 1, resistance as 0, missing as -1

	Parameters
	----------
	df_geno_pheno: pd.DataFrame
        Dataframe containing resistance values to be converted from "R" and "S" to 0 and 1

    drugs_list: list of str
        list of columns in df_geno_pheno containing the drug resistance info to be encoded

	Returns
	-------
	pd.Dataframe
        has same index as df_geno_pheno, columns corresponding to drugs_list
        contains numeric encoding of resistance values

    np.ndarray
        corresponds to above pd.DataFrame.values
	"""

    y_all_rs = df_geno_pheno[drugs_list]
    y_all_rs = y_all_rs.fillna('-1')
    y_all_rs = y_all_rs.astype(str)
    resistance_categories = {'R': 0, 'S': 1, '-1.0': -1, '-1': -1}

    y_all = y_all_rs.copy()
    for key, val in resistance_categories.items():
        y_all[y_all_rs == key] = val

    y_all.index = list(range(0, y_all.shape[0]))

    y_all_array = y_all.values

    return y_all, y_all_array


def alpha_mat(subset_y, df_geno_pheno, weight=1.):
    """
	creates alpha matrix (reflects proportion of strains resistant
	(-ve)/sensitive (+ve)).

	Parameters
	----------
	df_geno_pheno: pd.DataFrame
		Dataframe where last 23 columns contain R/S encoding
		of resistance vs sensitivity to antibiotics

	subset_y: np.ndarray
		Dataframe generated by the function rs_encoding_to_numeric
		containing matrix of resistance values (0 or 1) for each drug,
		then subset for indexes (strains) with phenotype data available
    weight: float
        Weight by which to multiply the sensitive class (to up or downweight
        sensitive relative to resistant strains)

	Returns
	-------
	np.ndarray of weighted resistance/sensitivity values proportionate to no.
		of strains with phenotypic data.
	"""
    # Drugs
    drugs = ['RIFAMPICIN', 'ISONIAZID', 'PYRAZINAMIDE',
             'ETHAMBUTOL', 'STREPTOMYCIN', 'LEVOFLOXACIN',
             'CAPREOMYCIN', 'AMIKACIN', 'MOXIFLOXACIN',
             'OFLOXACIN', 'KANAMYCIN', 'ETHIONAMIDE',
             'CIPROFLOXACIN']

    num_drugs = len(drugs)

    y_cnn = subset_y

    # generate alpha matrix
    alphas = np.zeros(num_drugs, dtype=np.float)
    alpha_matrix = np.zeros_like(y_cnn, dtype=np.float)

    for drug in range(num_drugs):

        resistant = len(np.squeeze(np.where(y_cnn[:, drug] == 0.)))
        sensitive = len(np.squeeze(np.where(y_cnn[:, drug] == 1.)))
        alphas[drug] = resistant / float(resistant + sensitive)
        alpha_matrix[:, drug][np.where(y_cnn[:, drug] == 1.0)] = weight * alphas[drug]
        alpha_matrix[:, drug][np.where(y_cnn[:, drug] == 0.0)] = - alphas[drug]

    return alpha_matrix


def make_geno_pheno_pkl(**kwargs):
    """
    Creates and saves a pd.DataFrame as a pkl that contains the numeric encoded
    phenotype information and the one-hot encoded sequence information for each isolate

    Required kwargs:
        phenotype_file: path to input phenotype file with columns "Isolate" and drug names
        genotype_input_directory: path to directory with input fasta files
        pkl_file: path to save the complete genotype/phenotype file
	"""

    output_path = kwargs['output_path']

    # get table for phenotypes
    df_phenos = pd.read_csv(kwargs['phenotype_file'], index_col="Isolate", sep=",", dtype=str).fillna(-1)

    # make table of all genotypes
    df_genos = make_genotype_df(kwargs['genotype_input_directory'])

    # to save on RAM, only save genotypes for isolates for which we have phenotypes
    isolate_ids = list(df_phenos.index)
    n_strains = len(isolate_ids)
    df_genos.index = df_genos.index.astype(str)
    df_genos = df_genos.loc[df_genos.index.intersection(isolate_ids)]

    # Drop rows where we're missing the sequence for a locus
    df_genos = df_genos.dropna(axis="index")

    #
    # # Apply one-hot encoding function to get each isolate sequence
    print('making one hot encoding for...')
    for column in df_genos.columns:
        print("...", column)
        df_genos[column + "_one_hot"] = df_genos[column].apply(np.vectorize(get_one_hot))

    # combined dataframe of all genotypes and phenotypes
    df_geno_pheno_full = df_genos.join(df_phenos, how='inner')

    pkl_file = kwargs["pkl_file"]
    df_geno_pheno_full.to_pickle(pkl_file)


def create_X(df_geno_pheno):
    """
	Create an input X matrix, with output dimensions:
		n_strains x 5 (one-hot) x longest locus length x no. of loci

    Parameters
    ----------
    df_geno_pheno: pd.DataFrame
        generated by make_geno_pheno_pkl, contains the numeric encoded
        phenotype information and the one-hot encoded sequence information for each isolate

    Returns
    -------
    np.ndarray
        with shape N_strains, 5, L_longest_locus, N_loci
        contains the one-hot encoded sequence information for each locus for each strain
	"""

    def _get_shapes(df_geno_pheno):
        """
		Finds the length of each gene in the input dataframe
		Parameters
		----------
		df_geno_pheno: pd.Dataframe

		Returns
		-------
		dict of str: int
			length of coordinates in each column
		"""
        shapes = {}
        for column in df_geno_pheno.columns:
            if "one_hot" in column:
                shapes[column] = df_geno_pheno.loc[df_geno_pheno.index[0], column].shape[0]

        return shapes

    shapes = _get_shapes(df_geno_pheno)

    # Length of longest gene locus
    n_genes = len(shapes)
    L_longest = max(list(shapes.values()))
    print("found n genes", n_genes, "and longest gene", L_longest)

    # Number of strains in model
    n_strains = df_geno_pheno.shape[0]

    # define shape of matrix - fill with zeros (effectively accomplishes padding)
    X = np.zeros((n_strains, 5, L_longest, n_genes))

    # for each strain and gene locus
    for idx, strain in enumerate(df_geno_pheno.index):
        for gene_index, gene in enumerate(shapes.keys()):
            one_hot_gene = df_geno_pheno.loc[strain, gene]
            X[idx, :, range(0, one_hot_gene.shape[0]), gene_index] = one_hot_gene

    return X


def masked_multi_weighted_bce(alpha, y_pred):
    """
	Calculates the masked weighted binary cross-entropy in multi-classification

	Parameters
	----------
	alpha: an element from the alpha matrix, a matrix of target y values weighted
		by proportion of strains with resistance data for any given drug
	y_pred: model-predicted y values
    weights: list of float (optional, default=[1., 1.])
        A list of two weights to be applied to the sensitive and resistant n_strains,
        respectively

	Returns
	-------
	scalar value of the masked weighted BCE.
	"""
    y_pred = K.clip(y_pred, K.epsilon(), 1.0 - K.epsilon())
    y_true_ = K.cast(K.greater(alpha, 0.), K.floatx())
    mask = K.cast(K.not_equal(alpha, 0.), K.floatx())
    num_not_missing = K.sum(mask, axis=-1)
    alpha = K.abs(alpha)
    bce = - alpha * y_true_ * K.log(y_pred) - (1.0 - alpha) * (1.0 - y_true_) * K.log(1.0 - y_pred)
    masked_bce = bce * mask
    return K.sum(masked_bce, axis=-1) / num_not_missing


def masked_weighted_accuracy(alpha, y_pred):
    """
	Calculates the mased weighted accuracy of a model's predictions
	Parameters
	----------
	alpha: an element from the alpha matrix, a matrix of target y values weighted
		by proportion of strains with resistance data for any given drug
	y_pred: model-predicted y values

	Returns
	-------
	scalar value of the masked weighted accuracy.
	"""

    total = K.sum(K.cast(K.not_equal(alpha, 0.), K.floatx()))
    y_true_ = K.cast(K.greater(alpha, 0.), K.floatx())
    mask = K.cast(K.not_equal(alpha, 0.), K.floatx())
    correct = K.sum(K.cast(K.equal(y_true_, K.round(y_pred)), K.floatx()) * mask)
    return correct / total

def load_alpha_matrix(alpha_matrix_path, y_array, df_geno_pheno, **kwargs):
    """
    Loads in the alpha matrix, if file exists, otherwise creates alpha matrix

    Parameters
    ----------
    alpha_matrix_path: str
        path to alpha matrix. Will be used to save matrix if matrix does not exist

    Returns
    -------
    np.ndarray
        The alpha matrix
    """

    if os.path.isfile(alpha_matrix_path):
        print("alpha matrix already exists, loading alpha matrix")
        alpha_matrix = alpha_matrix = np.loadtxt(alpha_matrix_path, delimiter=',')
    else:
        print("creating alpha matrix")
        if "weight_of_sensitive_class" in kwargs:
            print('creating alpha matrix with weight', kwargs["weight_of_sensitive_class"])
            alpha_matrix = alpha_mat(y_array, df_geno_pheno, kwargs["weight_of_sensitive_class"])
        else:
            alpha_matrix = alpha_mat(y_array, df_geno_pheno)
        np.savetxt(alpha_matrix_path, alpha_matrix, delimiter=',')

    print("the shape of the alpha_matrix: {}".format(alpha_matrix.shape))
    return alpha_matrix

def split_into_traintest(X_sparse, df_geno_pheno, category):
    """
    Splits the X dataframe into training and test set based on annotation in df_geno_pheno

    Parameters
    ----------
    X_sparse: sparse.COO
        a sparse-encoded np.ndarray containing genetic information for all isolates
    df_geno_pheno: pd.DataFrame
        Dataframe of genetic and phenotypic information. Contains the exact isolates in the exact order used to create X_sparse.
        Contains a column called "category" that will be used to split isolates into training and test
    category: str
        Name of the training set category

    Returns:
    -------
    sparse.COO
        a sparse-encoded np.ndarray containing genetic information for all TRAINING SET isolates
    sparse.COO
        a sparse-encoded np.ndarray containing genetic information for all TEST SET isolates
    """
    X_all = X_sparse.todense()

    df_geno_pheno = df_geno_pheno.reset_index(drop=True)

    train_indices = df_geno_pheno.query("category==@category").index
    test_indices = df_geno_pheno.query("category!=@category").index

    print("splitting X pkl")
    X_train = X_all[train_indices, :]
    X_test = X_all[test_indices, :]
    del X_all

    X_sparse_train = sparse.COO(X_train)
    sparse.save_npz(pkl_file_sparse_train, X_sparse_train, compressed=False)

    X_sparse_test = sparse.COO(X_test)
    sparse.save_npz(pkl_file_sparse_test, X_sparse_test, compressed=False)

    return X_sparse_train, X_sparse_test


def get_threshold_val(y_true, y_pred):
    """
    Compute the optimal threshold for prediction  based on the max sum of specificity and Sensitivity

    NB that we encoded R as 0, S as 1, so smaller predictions indicate higher chance of resistance

    We count falsely predicted resistance as a false positive, falsely predicted sensitivity as a false negative

    Parameters
    ----------
    y_true: np.array
        Actual labels for isolates
    y_pred: np.array
        Predicted labels for isolates

    Returns
    -------
    dict of str -> float with entries:
        sens: sensitivity at chosen threshold
        spec: specificity at chosen threshold
        threshold: chosen threshold value
    """

    num_samples = y_pred.shape[0]
    fpr_ = []
    tpr_ = []
    thresholds = np.linspace(0, 1, 101)
    num_sensitive = np.sum(y_true==1)
    num_resistant = np.sum(y_true==0)
    for threshold in thresholds:

        fp_ = 0 # number of false positives
        tp_ = 0 # number of true positives

        for i in range(num_samples):
            # If y is predicted resistant
            if (y_pred[i] < threshold):
                if (y_true[i] == 1): fp_ += 1
                if (y_true[i] == 0): tp_ += 1

        fpr_.append(fp_ / float(num_sensitive))
        tpr_.append(tp_ / float(num_resistant))

    fpr_ = np.array(fpr_)
    tpr_ = np.array(tpr_)

    # valid_inds = np.where(fpr_ <= 0.1)
    valid_inds = np.arange(101)
    sens_spec_sum = (1 - fpr_) + tpr_
    best_sens_spec_sum = np.max(sens_spec_sum[valid_inds])
    best_inds = np.where(best_sens_spec_sum == sens_spec_sum[valid_inds])

    if best_inds[0].shape[0] == 1:
        best_sens_spec_ind = np.array(np.squeeze(best_inds))
    else:
        best_sens_spec_ind = np.array(np.squeeze(best_inds))[-1]

    return {'threshold': np.squeeze(thresholds[valid_inds][best_sens_spec_ind]),
            'spec': 1 - fpr_[valid_inds][best_sens_spec_ind],
            'sens': tpr_[valid_inds][best_sens_spec_ind]}
