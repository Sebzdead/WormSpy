# Segment Anything (SAM 2 / 3 / 3.1) for the RIA axon

*2026-09-28. Question: should offline analysis (`src/wormspy/analysis/segmentation.py`) move to the
newest Segment Anything models to segment the RIA axon (nrD / nrV) in freely moving GCaMP
recordings at ~0.75 µm/px, 10 fps, widefield? Sources are numbered at the end; every version, date
and number below comes from the owner's paper, repo, model card or blog unless marked otherwise.*

---

## 1. TL;DR

**No, not as a drop-in upgrade.** At 0.75 µm/px the RIA neurite is a PSF-limited ridge about
1–2 px wide. No segmentation model can recover a width the optics never delivered, and a
pixel-precise mask of it would not improve ΔF/F anyway. The signal lives in the flux integrated
over a PSF-sized neighbourhood of the correct axonal domain. What matters is **placing the same
domain ROI on every frame** (registration and tracking) and **removing motion and expression
artefacts** (a ratiometric reference). SAM does not solve either of those better than cheaper
tools.

Concretely:

- **SAM 3 / 3.1's new feature, text/concept prompts, is useless here.** SAM 3 does not know
  biological terms: "nucleus" scores worse than "bright spot" on nuclei [11]. It also has no
  micro-SAM-style microscopy fine-tune yet [12a].
- **SAM 3.1's change is speed for many-object video.** It shares memory across object buckets
  and helps at 16–128 objects [2][4]. We track 1–3 objects.
- **All SAM variants resize the input to ~1 k px and work on 14–16 px tokens.** On a
  full 1920×1200 frame the whole nerve ring is about one token (§3.2). SAM only makes sense on a
  **crop around the head, upsampled**, and upsampling adds no information.
- **Worth doing instead, in order:** (1) a fixed-geometry domain ROI (a line or arc profile, as
  Hendricks et al. did [14]) placed by head registration, not by brightest-k pixels; (2) a
  co-expressed red reference (Liu et al. used GCaMP3 + mCherry in freely moving worms [15]);
  (3) a ridge filter (Hessian/Frangi [21]) for localisation; (4) *only if* localisation is still
  the failure point, a small benchmark of **SAM 2.1 video propagation on crops** (Apache-2.0,
  ungated, runs on MPS) and **micro-SAM ViT-B-LM fine-tuned with LoRA** on a few hundred frames.
  The experiment in §7 decides.
- **If nrD/nrV separation or the loop is the scientific target, the lever is optical:** more
  magnification or NA, shorter exposure, a red reference channel. Software comes second.

---

## 2. What the models actually are

| | SAM 2.1 | SAM 3 | SAM 3.1 |
|---|---|---|---|
| Released | 2024-09-29 checkpoints (SAM 2: 2024-07-29) [5] | arXiv 2025-11-20 [1] | 2026-03-27 [2][4] |
| What changed | Streaming memory for video; Hiera backbone [6] | Promptable **concept** segmentation (noun phrase and/or image exemplar); DETR-style detector + SAM 2-style tracker sharing one backbone; "presence" head [1][3] | "Object Multiplex": objects processed jointly in shared-memory buckets; fewer CPU–GPU syncs, better `torch.compile` [2][4]. Same architecture, new checkpoint. |
| Size | 38.9 M (tiny) – 224.4 M (large) [5] | 848 M ("0.9B" on HF) [3][7] | New checkpoint on the same model [2] |
| Input | 1024×1024 [8] | 1008×1008, ViT patch 14, 1024-dim, 32 layers [8] | same as SAM 3 [2] |
| Finest feature | High-res decoder features at stride 4 and 8; image embedding at stride 16 [8] | Neck upsamples the 72×72 token grid ×4 / ×2 → 288² (stride 3.5 px) [8] | same |
| Video memory | Memory bank of 7 past frames plus prompted frames (`num_maskmem: 7`) [8] | Memory bank of past and conditioning frames [1] | Shared memory across multiplexed objects [2] |
| Prompts | Points, boxes, masks | Text, exemplars, points, boxes, masks [3] | same |
| Speed (vendor) | 91.2 FPS tiny, 39.5 FPS large, **A100**, compiled [5] | 30 ms / image with 100+ objects on **H200**; video "near real-time for ~5 concurrent objects" [1] | ~7× at 128 objects on **H100** vs the Nov-2025 SAM 3; blog says 16 → 32 fps for "a medium number of objects" [2][4] |
| Licence | **Apache 2.0** (code, checkpoints, training code) [5] | **SAM License** (Meta custom): commercial use allowed; publications must acknowledge; trade-control and military/weapons exclusions; no reverse engineering [9a] | same SAM License |
| Checkpoint access | Direct download, ungated [5] | **Gated** on Hugging Face: share contact info, then `hf auth login` [3][7] | **Gated** (HF returns "restricted"; checked 2026-09-28) [2] |
| Apple MPS | Official notebooks support MPS with CPU fallback and a numerical-difference caveat [5] | Upstream **CUDA-only**: README requires CUDA ≥ 12.6; Triton and ~90 hard-coded `cuda` calls stop even `import` on macOS [3][10][10a] | Same. Community PR #627 adds MPS/CPU; **still open** as of 2026-09-25 [10a] |
| Fine-tuning | Training code released [5] | `pip install -e ".[train,dev]"` [3] | same repo |

Nothing newer than SAM 3.1 is announced. The repo's last commits (2026-09-18) are
video-tracker refactors. Meta's SAM 3.1 blog names no successor [4], and the repo has no tagged
releases. **No "SAM 3.2" or "SAM 4" exists.**

---

## 3. Why the model is not the bottleneck at 0.75 µm/px

### 3.1 Optics

The PSF FWHM is ≈ 0.51 λ/NA. At λ ≈ 515 nm (GCaMP emission) that is 1.05 µm at NA 0.25 and
0.88 µm at NA 0.30. The objective NA is not recorded here, so substitute the real value. A
0.2–0.5 µm neurite therefore images as a ridge about 1 µm wide, **1.2–1.4 px** at 0.75 µm/px,
before widefield defocus blur and motion blur. Nyquist for that PSF is about 0.4–0.5 µm/px, so
the rig **slightly undersamples**. A 1–2 px wide object carries no width or shape information. It
has a centreline, a peak and an integrated flux, and that is all any segmenter can return.

Motion blur is `v_head × t_exp` and is not removed by stage tracking, which follows the centroid,
not head swings. Illustrative values: 100 µm/s with 10 ms exposure gives 1 µm (1.3 px); with
50 ms it gives 5 µm (~7 px), which smears the ridge into a band. Take the real exposure from the
recording metadata. When blur is the dominant term, the fix is exposure or strobed excitation,
not a model.

What *is* resolvable is the **domain geometry**. The user estimates the loop at 15–25 px, so nrD
and nrV sit roughly 10–20 µm apart, which is many PSFs. This is the scale Liu et al. worked at
in freely moving worms with a **10× objective at 10 fps** [15], and Hendricks et al. with a 10×
objective, 10 ms exposures at 5 Hz [14]. The biology needs domain-level ROIs, and the rig
supports them.

### 3.2 What SAM sees

| Input to SAM | Effective µm per input px | Nerve ring (15–25 native px) | One ViT token (SAM 3, 14 px) |
|---|---|---|---|
| Full frame, resized 1920 → 1008 | 1.43 | 8–13 px, **< 1 token** | 20 µm |
| 64×64 px head crop → 1008 (×15.75) | 0.048 | 236–394 px ≈ 17–28 tokens | 0.67 µm (below the PSF) |

On full frames, SAM cannot represent the ring at all. The finest SAM 3 feature map has a stride
of 3.5 input px [8], which is about 5 µm on a resized full frame. On crops it can represent the
ring, but it is then looking at a smooth, upsampled, blurry blob. Its mask boundary follows
whatever contrast SAM decides is an edge. At low SNR that boundary moves with the signal itself,
so mask area correlates with brightness.

### 3.3 The measurement, not the mask, is where accuracy is lost

- **Brightest-k-pixel ROIs are biased.** With signal near background, the mean of the top 10 or
  25 pixels in an r = 10 px disc (~314 px) is dominated by the upper order statistics of the
  noise. That puts a noise-dependent floor under F, compresses ΔF/F, and makes the baseline
  depend on local background, including gut autofluorescence. A fixed-geometry ROI (a disc of
  about 2 × FWHM on the ridge, or a line/arc profile along the neurite as Hendricks et al. used
  [14]) has no such bias. SNR scales with √(pixels of real signal included). Mask precision
  finer than the PSF adds nothing.
- **Motion and expression artefacts dominate freely moving ΔF/F.** Liu et al. controlled for
  them with a GFP-only strain and a GCaMP3 + mCherry strain normalised by mCherry [15]. A red
  reference in RIA also gives a bright, activity-independent structure for localisation, which
  solves the identity and distractor problem that SAM would be asked to solve.
- **Published RIA axon work used manual ROIs.** Hendricks 2012: microfluidic 40× imaging at 5 Hz,
  frames aligned with StackReg, manually placed domain ROIs, (F − Fmin)/(Fmax − Fmin) [14].
  Freely moving: a line ROI along the process, on frames chosen by head deflection [14].
  Liu 2018: "the ROI center of each axonal domain was manually tracked using ImageJ plugin Manual
  Tracking" [15]. Automating that **placement** is the real gain available. Segmenting the neurite
  itself is not needed.

---

## 4. Evidence on SAM-family models in microscopy

| Work | What it shows for us |
|---|---|
| **micro-SAM** (Archit et al., *Nat Methods* 22:579–591, 2025) [12] | SAM ViT-B/L/H fine-tuned on LM and EM data, which clearly beats vanilla SAM. MIT licence. Supports CUDA and **MPS** (`micro_sam/util.py` picks cuda → mps → cpu). Includes LoRA/PEFT fine-tuning (`micro_sam/models/peft_sam.py`) and CPU fine-tuning from ~10 objects [12a]. **SAM 2 / SAM 3 support is still on the roadmap**, not shipped (latest release v1.8.14, 2026-09-06) [12a]. Trained and evaluated on cells, nuclei and organelles, not neurites. |
| **Archit & Pape, MIDL 2026** ("Revisiting foundation models for cell instance segmentation") [11] | Only head-to-head SAM 3 microscopy benchmark I found. Mean segmentation accuracy on fluorescence cells: **SAM 3 0.143 vs µSAM 0.347 vs Cellpose-SAM 0.363**. SAM 3 "lacks the knowledge of biological terms": on DSB, "nucleus" scores 0.085–0.387 vs "bright spot(s)" 0.465–0.509. SAM 2 automatic mask generation "did not segment any objects for several datasets". |
| **Kong et al. 2026** ("Is SAM3 ready for pathology segmentation?") [13] | "text-only prompts poorly activate nuclear concepts". Results are sensitive to visual-prompt type and budget, and a large gap remains to adapter-trained models. |
| **Cellpose-SAM** (Pachitariu, Rariden, Stringer, bioRxiv 2025) [16] | SAM's ViT backbone inside Cellpose, trained with augmentation for shot noise, blur and downsampling. Best generalist cell model in [11]. It is a **cell/nucleus** model; neurites are out of scope. |
| **CellSAM** (Israel et al., *Nat Methods* 22:2585–2593, 2025) [17] | A detector (CellFinder) prompts SAM. Cells only. |
| **MedSAM2** (Ma et al., arXiv 2504.03600) [18] | SAM 2 fine-tuned on 455k 3D pairs and 76k video frames. Evidence that SAM 2 video memory transfers to non-natural video **after fine-tuning**. |
| **HQ-SAM** (Ke et al., NeurIPS 2023) [19] | Meta's own-family finding: SAM's mask quality "falls short … particularly when dealing with objects that have intricate structures". This is the known thin-structure weakness. |
| Thin/tubular foundation models (e.g. vesselFM [20]) | Tubular shape priors beat generic SAM on vessels and axons, but in 3D, at far better sampling than ours. |

The pattern holds across sources. Zero-shot SAM (any version) underperforms domain-tuned models on
fluorescence, and SAM 3's text interface does not transfer to biology. Improvement comes from
fine-tuning or adapters on in-domain data. None of the evaluations covers sub-pixel neurites in
widefield. This question is outside the published evidence.

---

## 5. C. elegans tools: what exists and why it does not transfer directly

| Tool | Regime | Relevance |
|---|---|---|
| Leifer lab whole-brain freely moving (Nguyen 2016 [22]; Hallinen 2021 [23]); **fDNC** (Yu 2021 [24]) | Nuclear-localised GCaMP + RFP, 40× spinning-disk volumes; fDNC matches neuron *point clouds* (79.1% within-animal tracking, ~10 ms) | Point-correspondence thinking applies: track landmarks, not masks. The imaging regime is very different. |
| **Flavell lab BrainAlignNet / AutoCellLabeler** (Atanas et al., eLife [25]) | Deep registration of the bending head at single-pixel accuracy; 99.6% neuron alignment; >600× faster than their earlier approach | **The closest analogue to what we need**: register the head crop to a reference, then read fixed ROIs. Needs a structural channel. |
| **3DeeCellTracker** (Wen et al., eLife 2021 [26]) | 3D U-Net segmentation + FFN tracking; ~100 cells from one annotated volume | Again, nuclei in 3D. |
| **NeuroPAL** (Yemini et al., Cell 2021 [27]) | Multicolour ID landmarks | Only if the strain changes. |

None of these segments a neurite. They all rely on a **structural/reference channel** plus
**registration**, and the RIA literature does the same in reduced form (StackReg plus manual
ROIs) [14][15].

---

## 6. Compute for an 18,000-frame recording

| Method | Hardware | Throughput (source) | 18 k frames |
|---|---|---|---|
| Current classical pipeline (full frame, NLM dominant) | This Mac, CPU | ~100 ms/frame, measured 2026-09-28 on a synthetic 1920×1200 frame | ~30 min |
| SAM 2.1 tiny / large (video, compiled) | A100 | 91.2 / 39.5 FPS [5] | ~3.5 / ~8 min |
| SAM 3.1 (medium object count) | H100 | 32 fps [4] | ~9.5 min |
| SAM 3.1 video, 720p | Apple M5, MPS, unmerged PR #627 | ~1.3 fps, ~7 GB peak (one contributor's report) [10a] | **~3.8 h** |
| SAM 3.1 video | CPU | "~50× slower than MPS" [10a] | days |
| SAM 2.1 / micro-SAM ViT-B on MPS | Apple | **No primary number found**; supported, not benchmarked | unknown |

Cropping does not reduce SAM's backbone cost, because every input is resized to 1008 or 1024.
The cost is paid per frame regardless of crop size. For SAM 3 on a Mac, today you would be
running an unmerged community fork.

---

## 7. Suggested experiment

Settle the question with data before any code lands in `segmentation.py`.

**Data.** 3 recordings × 100 frames = **300 frames**, stratified: straight / ventral bend /
dorsal bend, high and low activity, frames with gut autofluorescence near the head, and
motion-blurred frames. Annotate on the raw 16-bit data: (a) the centreline of the RIA
loop/nrD/nrV as a polyline, and (b) a point at the centre of each domain. If a GFP-only or
GCaMP + mCherry RIA strain is available, include one recording as the no-activity control.

**Arms.**

1. **Classical, current** (`segment()` defaults).
2. **Classical, fixed ROI**: same localisation, but read a fixed disc/arc of ~2 × FWHM instead
   of brightest-k pixels.
3. **Ridge + registration**: Hessian/Frangi ridge filter (σ ≈ 1–1.5 px) on a head crop, rigid or
   affine registration to a reference frame, domain ROIs defined once on the reference.
4. **SAM 2.1 (tiny/base+) video on crops**: 96×96 px head crop around the classical centroid,
   box prompt on frame 0, re-prompt on failure. Apache-2.0, runs on MPS.
5. **micro-SAM ViT-B-LM, zero-shot and LoRA fine-tuned** on 200 of the 300 frames (test on the
   held-out 100 and on the held-out recording).
6. *(Optional, needs a CUDA box)* **SAM 3.1** with point/box prompts on crops. Skip text prompts,
   per [11][13].

**Metrics.**

| Question | Metric |
|---|---|
| Does it find the right structure? | Localisation error to the annotated domain centre (px, median and 95th percentile); **identity-swap rate** (fraction of frames locked onto gut or another structure) |
| Is the tracking stable? | Frame-to-frame centroid jitter vs annotated jitter; number of re-acquisitions |
| Does the mask overlap the neurite? | Centreline recall within 1.5 px (clDice-style) rather than IoU. IoU is meaningless for a 1–2 px object. |
| **Is the trace better?** (the one that matters) | Correlation of each arm's nrD/nrV trace with the trace from **manual ROIs** on the same frames; on the GFP/mCherry control, residual variance and correlation with head curvature (should be ~0) |
| Cost | s/frame on the Mac (MPS) and on CUDA if available; annotation hours for arm 5 |

**Decision rule.** Adopt a SAM arm only if it beats arm 3 on identity-swap rate **and** on trace
correlation with manual ROIs by a margin larger than the recording-to-recording spread, at an
acceptable per-recording runtime. Otherwise keep arm 2 or 3 and spend the effort on the reference
channel and exposure.

---

## 8. Confidence and what I could not verify

- **High confidence**: release dates and versions (SAM 2.1 2024-09-29, SAM 3 arXiv 2025-11-20,
  SAM 3.1 2026-03-27); SAM 3 input 1008 / patch 14 / neck scales (read from `model_builder.py`);
  SAM 2.1 1024 input and memory size (from the config yaml); licences; HF gating; SAM 3's
  CUDA-only upstream status; RIA imaging methods (quoted from the PMC full texts of [14][15]).
- **Medium**: SAM 3.1 speed numbers. The blog's "16 → 32 fps" and the release note's "~7× at 128
  objects" are Meta's own H100 benchmarks at object counts far from ours. The MPS figure (1.3 fps)
  is one contributor's report on an unmerged PR.
- **Not verified**:
  - Whether the facebook/sam3.1 gate is identical to sam3's. The page returns "restricted", and I
    did not request access.
  - SAM 2.1 or micro-SAM throughput on MPS: no primary benchmark found.
  - Any published evaluation of SAM (any version) on sub-pixel neurites in widefield. None found.
    §3 is first-principles reasoning, not literature.
  - The SAM 3 paper's venue (secondary sources say ICLR 2026; not confirmed from a primary
    source) and the full micro-SAM Nat Methods text (paywalled). Micro-SAM claims come from the
    preprint, docs and repo.
  - The rig's objective NA and exposure time. The PSF and blur numbers in §3.1 are parametric;
    substitute the real values.
  - Liu et al.'s freely moving RIA frames were at 10×, but the camera pixel size was not
    extracted, so their exact µm/px is unknown.
- **Local measurement**: the ~100 ms/frame classical timing was one run on a synthetic frame with
  whatever Python `conda run -n wormspy` resolved. Treat it as an order of magnitude.

---

## Sources

1. Carion et al., "SAM 3: Segment Anything with Concepts", arXiv:2511.16719 (v1 2025-11-20, v2 2026-03-28). https://arxiv.org/abs/2511.16719
2. facebookresearch/sam3, `RELEASE_SAM3p1.md`. https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md
3. facebookresearch/sam3 README (params, requirements, gated checkpoints, fine-tuning). https://github.com/facebookresearch/sam3
4. Meta AI blog, "SAM 3.1: Faster and More Accessible Real-Time Video Detection and Tracking With Multiplexing and Global Reasoning", 2026-03-27. https://ai.meta.com/blog/segment-anything-model-3/
5. facebookresearch/sam2 README (SAM 2.1 checkpoints, speeds on A100, Apache 2.0, training code). https://github.com/facebookresearch/sam2
6. Ravi et al., "SAM 2: Segment Anything in Images and Videos", arXiv:2408.00714. https://arxiv.org/abs/2408.00714
7. Hugging Face model card, facebook/sam3 (gated, 0.9B, Transformers integration). https://huggingface.co/facebook/sam3 ; SAM 3.1 checkpoints: https://huggingface.co/facebook/sam3.1
8. Source configs: `sam3/model_builder.py` (ViT img_size 1008, patch_size 14, embed_dim 1024, depth 32; neck scale_factors [4, 2, 1, 0.5]) https://github.com/facebookresearch/sam3/blob/main/sam3/model_builder.py ; `sam2/configs/sam2.1/sam2.1_hiera_l.yaml` (image_size 1024, num_maskmem 7, use_high_res_features_in_sam) https://github.com/facebookresearch/sam2/blob/main/sam2/configs/sam2.1/sam2.1_hiera_l.yaml

9a. SAM License. https://github.com/facebookresearch/sam3/blob/main/LICENSE

10. sam3 issue #164, backend-agnostic inference. https://github.com/facebookresearch/sam3/issues/164

10a. sam3 PR #627, "Run inference on Apple Silicon (MPS) and CPU" (open). https://github.com/facebookresearch/sam3/pull/627
11. Archit & Pape, "Revisiting foundation models for cell instance segmentation", MIDL 2026, arXiv:2603.17845. https://arxiv.org/abs/2603.17845
12. Archit et al., "Segment Anything for Microscopy", *Nat Methods* 22:579–591 (2025), doi:10.1038/s41592-024-02580-4; preprint https://www.biorxiv.org/content/10.1101/2023.08.21.554208v1

12a. micro-sam repo and docs (roadmap, MPS device selection, `peft_sam.py`, releases). https://github.com/computational-cell-analytics/micro-sam ; https://computational-cell-analytics.github.io/micro-sam/micro_sam.html
13. Kong et al., "Is SAM3 ready for pathology segmentation?", arXiv:2604.18225. https://arxiv.org/abs/2604.18225
14. Hendricks, Ha, Maffey, Zhang, "Compartmentalized calcium dynamics in a C. elegans interneuron encode head movement", *Nature* 487:99–103 (2012). https://pmc.ncbi.nlm.nih.gov/articles/PMC3393794/
15. Liu et al., "Cholinergic sensorimotor integration regulates olfactory steering", *Neuron* 97:390–405 (2018). https://pmc.ncbi.nlm.nih.gov/articles/PMC5773357/
16. Pachitariu, Rariden, Stringer, "Cellpose-SAM: superhuman generalization for cellular segmentation", bioRxiv 2025.04.28.651001. https://www.biorxiv.org/content/10.1101/2025.04.28.651001v1
17. Israel et al., "CellSAM: a foundation model for cell segmentation", *Nat Methods* 22:2585–2593 (2025). https://www.nature.com/articles/s41592-025-02879-w
18. Ma et al., "MedSAM2: Segment Anything in 3D Medical Images and Videos", arXiv:2504.03600. https://arxiv.org/abs/2504.03600
19. Ke et al., "Segment Anything in High Quality", NeurIPS 2023, arXiv:2306.01567. https://arxiv.org/abs/2306.01567
20. vesselFM, arXiv:2411.17386. https://arxiv.org/abs/2411.17386
21. Frangi et al., "Multiscale vessel enhancement filtering", MICCAI 1998, LNCS 1496:130–137. https://doi.org/10.1007/BFb0056195
22. Nguyen et al., "Whole-brain calcium imaging with cellular resolution in freely behaving *C. elegans*", *PNAS* 113:E1074 (2016). https://doi.org/10.1073/pnas.1507110112
23. Hallinen et al., "Decoding locomotion from population neural activity in moving C. elegans", *eLife* 10:e66135 (2021). https://elifesciences.org/articles/66135
24. Yu et al., "Fast deep neural correspondence for tracking and identifying neurons in C. elegans using semi-synthetic training", *eLife* 10:e66410 (2021). https://elifesciences.org/articles/66410
25. Atanas et al., "Deep neural networks to register and annotate cells in moving and deforming nervous systems", *eLife* reviewed preprint 108159 (year not confirmed). https://elifesciences.org/articles/108159
26. Wen et al., "3DeeCellTracker…", *eLife* 10:e59187 (2021). https://elifesciences.org/articles/59187
27. Yemini et al., "NeuroPAL: a multicolor atlas for whole-brain neuronal identification in C. elegans", *Cell* 184:272–288 (2021). https://doi.org/10.1016/j.cell.2020.12.012
