# Stage 1 — reproduce a ChIP-seq slice of a published study

**Paper:** Wang et al., *"A DNA tumor virus globally reprograms host 3D genome
architecture to achieve immortal growth,"* Nat Commun 2023.
PMID 36949074 · PMC10033825 · DOI 10.1038/s41467-023-37347-6.

Goal: have Caliper run a real ChIP-seq analysis on the paper's **own deposited data**
and verify against the paper's published findings — an early, checkable win that also
seeds the first real calibration set.

## Why ChIP-seq, not RNA (resolved from the paper)

The Methods state: *"ChIP-seq and RNA-seq data in Hi-C analysis was downloaded from the
ENCODE project ... or generated in our lab previously."* So the paper's **RNA-seq is
reused public data** (ENCODE GM12878 LCL, hg19, plus prior lab work — Zhao et al. 2011
PNAS), **not deposited in GSE128952 and with no published DE-gene table.** The data that
is genuinely theirs, cleanly deposited, and matches published figures is the
**ChIP-seq / CUT&RUN in GSE128952** — so that is the right Stage-1 target.

## Data (GSE128952) — concrete samples

Primary target — **RAD21 ChIP-seq (cohesin), EBNA3A ON vs OFF, with input controls** (a
clean MACS2 peak-calling setup):

| GSM | Sample | Role |
|-----|--------|------|
| GSM5456437 / 38 | EBNA3A 4HT (ON) ChIP-seq RAD21 Rep1/2 | treatment, EBNA3A active |
| GSM5456439 / 40 | EBNA3A OFF ChIP-seq RAD21 Rep1/2 | treatment, EBNA3A inactivated |
| GSM5456441 | EBNA3A 4HT (ON) ChIP-seq INPUT | control |
| GSM5456442 | EBNA3A OFF ChIP-seq INPUT | control |

Backup / extension — CUT&RUN (uses SEACR rather than MACS2): CTCF (GSM5456433–436) and
H3K27ac (GSM5456429–432), each EBNA3A ON/OFF Rep1/2.

## Answer key (what "correct" means)

The paper reports that **EBNA3 inactivation reduces CTCF/RAD21 (cohesin) DNA binding**
and rewires looping at specific loci (CDKN2A/B, AICDA). So Stage-1 verification is
directional and locus-level:

1. Call RAD21 peaks for EBNA3A **ON** and **OFF** (treatment vs input, both reps).
2. Confirm the **direction matches the paper**: differential RAD21 binding between ON and
   OFF in the reported direction (EBNA3A active vs inactivated).
3. Sanity: RAD21 peaks should **overlap CTCF** sites (cohesin co-localizes with CTCF) —
   check with `bedtools intersect`.
4. Locus check: inspect the paper's highlighted loci (e.g., **CDKN2A**, ref 19; AICDA).

(If the domain expert later shares processed peak files, we upgrade to a direct peak-set overlap.)

## Pipeline (what Caliper runs)

Runtime: `environments/bio-chip.yml`. Align to **hg19** (the paper's build) so coordinates
compare to its figures.
`sra-tools` (fetch FASTQ for the GSMs) → `fastqc`/`fastp` (QC/trim) → `bwa`/`bowtie2`
(align to hg19) → `samtools` (sort/index) → `macs2 callpeak` (treatment vs input) →
`bedtools` (ON-vs-OFF differential, CTCF overlap) → compare to answer key.

## Compute

Linux x86-64 (bioconda native). ChIP FASTQs are modest (a few GB each), so this is
**lighter than RNA/STAR** — a 128 GB box is ample headroom; even 64 GB would do.
Transient AWS EC2 via `scripts/aws_ec2.sh` (use `environments/bio-chip.yml` in `setup`).

## Calibration tie-in

Each run writes a provenance JSON. `export_review_sheet("runs","review.csv")` → the domain expert
marks `correct` → `calibrate_from_runs("runs","review.csv")` → a real trust gate
(`caliper/trust/review.py`).

## Immediate next actions

1. Launch the EC2 and build the `caliper-bio-chip` env.
2. Fetch the 6 RAD21 GSMs + inputs; run the pipeline on Rep1 first, then both reps.
3. Verify ON-vs-OFF direction + CTCF overlap against the paper; record runs for calibration.
