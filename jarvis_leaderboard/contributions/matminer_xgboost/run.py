"""Module to run matminer results."""
#%%
import random
import os
import shutil
import pandas as pd
from tqdm import tqdm
import csv
import numpy as np
import math
from jarvis.ai.pkgs.utils import regr_scores
from jarvis.db.figshare import data
from jarvis.core.atoms import Atoms
import zipfile
import json
import time

tqdm.pandas()


task = 'SinglePropertyClass' # 'SinglePropertyClass'


#%%
'''
Define regressor and featurizer
'''

from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
import xgboost as xgb



#%%
'''
Model
'''
if task == 'SinglePropertyPrediction':
    model =  xgb.XGBRegressor
elif task == 'SinglePropertyClass':
    model =  xgb.XGBClassifier


n_estimators = 10000
num_parallel_tree = 8
learning_rate = 0.1  
tree_method = 'gpu_hist'   # gpu_hist or hist
reg = Pipeline([
            ('imputer', SimpleImputer()), 
            ('scaler', StandardScaler()),
            ('model', model(
                            # n_jobs=-1, random_state=0,
                            n_estimators=n_estimators, learning_rate=learning_rate,
                            reg_lambda=0.01,reg_alpha=0.01,
                            subsample=0.85,colsample_bytree=0.3,colsample_bylevel=0.5,
                            num_parallel_tree=num_parallel_tree,
                            tree_method=tree_method,
                            ))
        ])

#%%

def to_unitcell(structure):
    '''
    Make sure coordinates are within the unit cell.
    Used before using structural featurizer.

    Parameters
    ----------
    structure :  pymatgen.core.structure.Structure

    Returns
    -------
    structure :  pymatgen.core.structure.Structure
    '''    
    [site.to_unit_cell(in_place=True) for site in structure.sites]
    return structure

# https://github.com/mathsphy/paper-ml-robustness-material-property/blob/main/myfunc.py
def StructureFeaturizer(
    df_in, col_id="structure", ignore_errors=True, chunksize=35, index_ids=None
):
    """
    Featurize a dataframe using Matminter Structure featurizer

    Parameters
    ----------
    df : Pandas.DataFrame
        DataFrame with a column named "structure"

    Returns
    -------
    A DataFrame containing 273 features (columns)

    """
    # For featurization
    from matminer.featurizers.base import MultipleFeaturizer
    from matminer.featurizers.composition import (
        ElementProperty,
        Stoichiometry,
        ValenceOrbital,
        IonProperty,
    )
    from matminer.featurizers.structure import (
        SiteStatsFingerprint,
        StructuralHeterogeneity,
        ChemicalOrdering,
        StructureComposition,
        MaximumPackingEfficiency,
    )
    
    # if not provided, use all the ids in the dataframe
    if index_ids is None:
        index_ids = df_in.index


    if isinstance(df_in, pd.Series):
        df = df_in.loc[index_ids].copy().to_frame()
    else:
        df = df_in.loc[index_ids].copy()
    

    # can we apply this to QM9?
    df[col_id] = df[col_id].apply(to_unitcell)

    # 128 structural feature
    struc_feat = [
        SiteStatsFingerprint.from_preset("CoordinationNumber_ward-prb-2017"),
        SiteStatsFingerprint.from_preset(
            "LocalPropertyDifference_ward-prb-2017"
        ),
        StructuralHeterogeneity(),
        MaximumPackingEfficiency(),
        ChemicalOrdering(),
    ]
    # 145 compositional features
    compo_feat = [
        StructureComposition(Stoichiometry()),
        StructureComposition(ElementProperty.from_preset("magpie")),
        StructureComposition(ValenceOrbital(props=["frac"])),
        StructureComposition(IonProperty(fast=True)),
    ]
    featurizer = MultipleFeaturizer(struc_feat + compo_feat)

    # Set the chunksize used for Pool.map parallelisation
    featurizer.set_chunksize(chunksize=chunksize)
    featurizer.fit(df[col_id])
    X = featurizer.featurize_dataframe(
        df=df, col_id=col_id, ignore_errors=ignore_errors
    )
    # check failed entries
    print("Featurization completed.")
    failed = np.any(pd.isnull(X.iloc[:, df.shape[1] :]), axis=1)
    if np.sum(failed) > 0:
        print(f"Number failed: {np.sum(failed)}/{len(failed)}")
    return X, failed


#%%    
# get the available properties for the database db
def get_props(db):
    dir = f"../../benchmarks/AI/{task}"
    # get all the files that starts with db and ends with .json.zip in dir
    files = [f for f in os.listdir(dir) if f.startswith(db) and f.endswith(".json.zip")]
    # remove the db name and .json.zip from the file name
    files = [f.replace(db+"_", "").replace(".json.zip", "") for f in files]
    return files 

#%%
for db in ['dft_3d' ]: #'hmof','qm9','megnet','qe_tb', 'dft_3d', 'ssub',

    # Get the whole dataset and featurize for once and for all properties 
        
    if db == 'ssub':
        dat = pd.read_json('ssub.json')
        n_features = 145
    else:
        dat = data(db)
        n_features = 273


    X_file = f"X_{db}.csv"
    if not os.path.exists(X_file):
        structure = f'structure_{db}.pkl'
        if os.path.exists(structure):
            df = pd.read_pickle(structure)
        else:
            
            df = pd.DataFrame(dat)
            df["structure"] = df["atoms"].progress_apply(
                lambda x: (
                    (Atoms.from_dict(x)).get_primitive_atoms
                ).pymatgen_converter()
            )
            df.to_pickle(structure)

        df = df.sample(frac=1, random_state=123)
        X, failed = StructureFeaturizer(df)
        X.to_csv(X_file)

    df = pd.read_csv(X_file)
    features = df.columns[-n_features:]
    if 'id' in df.columns:
        df['id'] = df['id'].astype(str)
        df = df.set_index('id')
    elif 'jid' in df.columns:
        df = df.set_index('jid')

    for prop in get_props(db):   
    # for prop in ['slme']:

        print("Running", db, prop)

        if task == 'SinglePropertyPrediction':
            fname = f"AI-{task}-{prop}-{db}-test-mae.csv"
        elif task == 'SinglePropertyClass':
            fname = f"AI-{task}-{prop}-{db}-test-acc.csv"

        # skip this loop if the file already exists
        if os.path.exists(fname) or os.path.exists(fname + ".zip"):
            print("Benchmark already done, skipping", fname)
            continue

        json_zip = f"../../benchmarks/AI/{task}/{db}_{prop}.json.zip"
        temp2 = f"{db}_{prop}.json"
        zp = zipfile.ZipFile(json_zip)   
        train_val_test = json.loads(zp.read(temp2))

        train = train_val_test["train"]
        if 'val' in train_val_test:
            val = train_val_test["val"]
        else:
            val = {}
        test = train_val_test["test"]

        n_train = len(train)
        n_val = len(val)
        n_test = len(test)

        print("number of training samples", len(train))
        print("number of validation samples", len(val))
        print("number of test samples", len(test))

        ids = list(train.keys()) + list(val.keys()) + list(test.keys())
        y = list(train.values()) + list(val.values()) + list(test.values())
        X = df.loc[ids,features]

        X = np.array(X)
        y = np.array(y).reshape(-1, 1).astype(np.float64)
        id_test = ids[-n_test:]

        X_train = X[:n_train]
        y_train = y[:n_train]

        X_val = X[-(n_val + n_test) : -n_test]
        y_val = y[-(n_val + n_test) : -n_test]

        X_test = X[-n_test:]
        y_test = y[-n_test:]

        t1 = time.time()
        reg.fit(X_train, y_train)
        
        pred = reg.predict(X_test)

        f = open(fname, "w")
        line = "id,prediction\n"
        f.write(line)
        for j, k in zip(id_test, pred):
            line = str(j) + "," + str(k) + "\n"
            f.write(line)
        f.close()
        t2 = time.time()
        print("Time", t2 - t1)
        cmd = "zip " + fname + ".zip " + fname
        os.system(cmd)
        
        # remove fname
        os.remove(fname)

        if task == 'SinglePropertyPrediction':
            reg_sc = regr_scores(y_test, pred)
            print(prop, reg_sc["mae"])
        elif task == 'SinglePropertyClass':
            from sklearn.metrics import accuracy_score
            acc = accuracy_score(y_test, pred)
            print(prop, acc)


# %%
