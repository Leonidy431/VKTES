# Algorithm Research: [Feature Name]

**Research Phase Completion Date:** [DATE]  
**Researcher(s):** [Names]  
**Problem Statement:**  
[One paragraph: what problem needs solving, why existing solutions are insufficient]

---

## 1. Literature Review — PubMed & Google Scholar

### 1.1 Search Strategy
**Keywords used:**
- `snow depth estimation UAV`
- `tethered drone autonomous rooftop`
- `thermal imaging snow classification`
- `reinforcement learning path planning SLAM`

**Databases:**
- PubMed: https://pubmed.ncbi.nlm.nih.gov/ (biomedical + physics)
- Google Scholar: https://scholar.google.com/
- arXiv: https://arxiv.org/ (preprints)
- IEEE Xplore: https://ieeexplore.ieee.org/ (engineering)

**Date range:** [Start year] – [Current year]  
**Results:** [N papers] screened, [M papers] selected

---

### 1.2 Selected Papers (≥5)

#### Paper 1
- **Title:** [Full title]
- **Authors:** [Author et al.]
- **Year:** [YYYY]
- **DOI:** https://doi.org/10.xxxx/xxxxx
- **Abstract Summary:**  
  [1–2 sentences]
- **Key Findings Relevant to Boreas:**
  - Finding 1: [Detail]
  - Finding 2: [Detail]
  - Finding 3: [Detail]
- **Algorithm Proposed:** [Name/description]
- **Performance Metrics:** [Accuracy, latency, etc.]
- **Limitations for Boreas:** [Hardware, weather, etc.]

#### Paper 2
[Same format as Paper 1]

#### Paper 3–5
[Same format]

---

### 1.3 Systematic Comparison Table

| Paper | Algorithm | Accuracy | Latency | Real-Time? | Weather Robust? | Code Available? | Boreas Fit |
|-------|-----------|----------|---------|------------|-----------------|-----------------|-----------|
| Paper 1 | Method A | RMSE 8cm | 250ms | Soft | Moderate | Yes (GitHub) | Good |
| Paper 2 | Method B | 95% F1 | 45ms | Hard | Low (fog) | No | Excellent |
| Paper 3 | Method C | 87% acc | 1s | Soft | High | Yes (Zenodo) | Poor |
| ... | ... | ... | ... | ... | ... | ... | ... |

---

## 2. Domain Constraints for Boreas

### 2.1 Hardware Constraints
- **Companion Computer:** Raspberry Pi-class CPU (~1.5 GHz ARM, 4 GB RAM)
- **Cameras:** FLIR Lepton 3.5 (160×120, 9 fps), USB camera (VGA/HD)
- **LiDAR:** ORB-SLAM depth estimation or external sensor (if available)
- **Accelerators:** Hailo-8L (13 TOPS AI, <2.5W)
- **Network:** 400V DC tether (EMI-prone); no WiFi

### 2.2 Operational Constraints
- **Real-time requirement:** 10 Hz main loop (100ms budget per tick)
- **Safety check:** 20 Hz (50ms), GFCI trip deadline
- **Flight time:** ~45 s on backup power (tight)
- **Weather:** -30°C to +20°C (snow clearing season)
- **Wind:** Up to 15 m/s (roof cleaning operations)
- **Payload:** Max 0.5 kg additional sensors

### 2.3 Algorithm Integration Points
- **TiltController:** Needs snow type + depth in <50ms
- **BurstVibrator:** Triggers based on depth classification
- **HoverBlowController:** Selects altitude/throttle by depth
- **PerceptionModule:** Outputs roof boundary + obstacles + snow grid

---

## 3. Candidate Algorithms

### 3.1 Candidate A: [Name]
**Source:** [Paper 1, custom, ...]  
**Principle:** [Brief description]  
**Pseudocode:**
```
Input: lidar_points, rgb_image
Output: snow_depth_map, snow_type

1. Filter LiDAR to roof plane (RANSAC)
2. Compare point heights to reference surface
3. Aggregate depth in 0.5m grid cells
4. Classify each cell by texture + temperature
Return depth_map, classification_map
```
**Pros:**
- [Pro 1]
- [Pro 2]

**Cons:**
- [Con 1]
- [Con 2]

**Implementation Complexity:** O(n log n) for n points  
**Estimated Latency:** 120ms  
**Estimated Power:** 0.8W  

---

### 3.2 Candidate B: [Name]
[Same template]

### 3.3 Candidate C: [Name]
[Same template]

---

## 4. Proof-of-Concept Test Plan

### 4.1 Test Data
- **Synthetic:** [Link to synthetic dataset in `test_data/`]
- **Field Data:** [Location, date, weather conditions]
  - 5 × snow depth points + photos (ground truth)
  - Continuous 5-min flight video + thermal
  - Wind speed + temperature log

### 4.2 Evaluation Metrics
- **Accuracy:** RMSE [cm], MAE [cm], R² vs. ground truth
- **Latency:** Cumulative histogram (p50, p95, p99) [ms]
- **Power:** Peak + average over 60-sec flight [W]
- **Robustness:** 
  - Accuracy variance across [N] runs [%]
  - Accuracy @ -20°C vs. +5°C [delta]
  - Accuracy with synthetic noise added [dB SNR threshold]

### 4.3 Pass Criteria
- [ ] Accuracy within ±20% of paper-reported performance
- [ ] Latency ≤50ms (p95) on Raspberry Pi
- [ ] Power draw <1.5W sustained
- [ ] No memory leaks over 5-min continuous operation
- [ ] Graceful fallback if sensor fails

---

## 5. Decision Record

**Recommended Candidate:** [A / B / C]  
**Confidence:** [Low / Medium / High]  
**Specialist Consensus:** [Unanimous / N-to-M split]  
**Next Step:** Phase 7–9 Multi-Expert Evaluation Matrix

**If Low Confidence:** Additional research required on [topic]

---

## 6. References

All papers cited above are listed in `EVALUATION_MATRIX_TEMPLATE.csv` and will be documented in the final `algorithm_decisions/{feature}.json` file.

**To Update This Research:**
1. Add new papers as they're found
2. Re-run PoC with updated candidate
3. Update decision record
4. Trigger re-evaluation if accuracy diverges >5% from prediction
