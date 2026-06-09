# Caliper Lab Pack (data plane)

The single artifact installed on each lab server. The control plane (the brain) stays
central and dispatches compute here; **your data never leaves this machine**.

It bundles the bioinformatics toolset for the supported assays plus a trivial runner —
**no trust/calibration logic, no product IP** — so it's safe to ship as a binary image.

## What's inside
- **Tools** (`environment.yml`, one pinned env covering all assays):
  - RNA-seq — `salmon`, `STAR`, `hisat2`, `subread` (featureCounts)
  - ChIP-seq / ATAC-seq — `bowtie2`, `bwa`, `MACS3`, `bedtools`
  - DNA-seq — `bwa`, `gatk4`, `bcftools`
  - QC — `fastqc`, `multiqc`, `fastp`; utilities — `samtools`, `pysam`; stats — numpy/pandas/scipy/statsmodels/matplotlib
- **`runner.py`** — executes a dispatched step, streams progress to `status.json`. (Canonical copy lives at `caliper/lab/runner.py`.)

## Install on a lab (one command)
```bash
curl -fsSL https://get.caliper.morphmind.ai/install.sh | bash
```
Prefers a reproducible **Apptainer/Singularity** image (rootless, HPC-friendly); falls
back to a pinned **conda** env where no container runtime exists. It prints the
`CALIPER_REMOTE_*` config to register the lab with the control plane.

## Build the image (maintainers)
```bash
docker build -f lab-pack/Containerfile -t caliper-lab:1.0 .
apptainer build caliper-lab.sif docker-daemon://caliper-lab:1.0   # -> the shipped .sif
```

## Safety
- Writes are confined to the lab workspace; input data is read-only.
- Reference genomes/indices are large and per-lab — built once on the lab by a recipe,
  never stored in this repo.

## Validated (on the first lab)
RNA-seq differential expression, **MACS3 peak-calling (ChIP/ATAC)**, and **bwa→bcftools
variant-calling (DNA-seq)** all run end-to-end from this toolset.
