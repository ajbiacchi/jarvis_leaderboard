#!/bin/bash
#SBATCH --time=59:00:00
#SBATCH --mem=30G
#SBATCH --gres=gpu:1
#SBATCH --partition=singlegpu
#SBATCH --error=job.err
#SBATCH --output=job.out
. ~/.bashrc
export TMPDIR=/scratch/$SLURM_JOB_ID
cd /wrk/knc6/Software/alignn_calc/jarvis_leaderboard/jarvis_leaderboard/contributions/alignn_model/OCP/temp10ka/cgcnn_pred
conda activate ocp-models
python pred2.py

