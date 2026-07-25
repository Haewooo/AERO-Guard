# Marshalling classifier — evaluation report

- Corpus: **SYNTHETIC generator**, 440 labelled sequences
- Non-signal corpus: 12 motion types x 40 seeds
- Regenerate: `python -m evaluation.evaluate_classifier`

> **These numbers are synthetic.** The sequences come from the same
> generator the classifier's thresholds were tuned against, so the
> in-distribution accuracy below is a consistency check and must not
> be quoted as field accuracy. The degradation and false-positive
> sections probe conditions the generator does not model and are the
> meaningful results until field recordings exist.

## 1. In-distribution confusion matrix

Overall accuracy: **100.0%** (440/440)

| truth \ predicted | all_clear | chocks_inserted | chocks_removed | cut_engines | emergency_stop | move_ahead | slow_down | start_engines | stop | turn_left | turn_right |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all_clear | 40 | · | · | · | · | · | · | · | · | · | · |
| chocks_inserted | · | 40 | · | · | · | · | · | · | · | · | · |
| chocks_removed | · | · | 40 | · | · | · | · | · | · | · | · |
| cut_engines | · | · | · | 40 | · | · | · | · | · | · | · |
| emergency_stop | · | · | · | · | 40 | · | · | · | · | · | · |
| move_ahead | · | · | · | · | · | 40 | · | · | · | · | · |
| slow_down | · | · | · | · | · | · | 40 | · | · | · | · |
| start_engines | · | · | · | · | · | · | · | 40 | · | · | · |
| stop | · | · | · | · | · | · | · | · | 40 | · | · |
| turn_left | · | · | · | · | · | · | · | · | · | 40 | · |
| turn_right | · | · | · | · | · | · | · | · | · | · | 40 |

### Per class

| signal | n | precision | recall | F1 |
|---|---|---|---|---|
| all_clear | 40 | 1.000 | 1.000 | 1.000 |
| chocks_inserted | 40 | 1.000 | 1.000 | 1.000 |
| chocks_removed | 40 | 1.000 | 1.000 | 1.000 |
| cut_engines | 40 | 1.000 | 1.000 | 1.000 |
| emergency_stop | 40 | 1.000 | 1.000 | 1.000 |
| move_ahead | 40 | 1.000 | 1.000 | 1.000 |
| slow_down | 40 | 1.000 | 1.000 | 1.000 |
| start_engines | 40 | 1.000 | 1.000 | 1.000 |
| stop | 40 | 1.000 | 1.000 | 1.000 |
| turn_left | 40 | 1.000 | 1.000 | 1.000 |
| turn_right | 40 | 1.000 | 1.000 | 1.000 |

## 2. Degradation outside the generator's assumptions

MediaPipe landmark jitter in normalised image coordinates. The generator uses sigma=0.004; a real webcam is closer to 0.01-0.03.

| landmark noise sigma | accuracy |
|---|---|
| 0.004 | 100.0% |
| 0.010 | 100.0% |
| 0.020 | 92.5% |
| 0.030 | 64.3% |
| 0.050 | 43.0% |

| horizontal scale (off-axis) | accuracy |
|---|---|
| 1.0 | 100.0% |
| 0.9 | 100.0% |
| 0.8 | 100.0% |
| 0.7 | 100.0% |
| 0.6 | 100.0% |
| 0.5 | 91.1% |

| frames lost (tracker drop-out) | accuracy |
|---|---|
| 0% | 100.0% |
| 10% | 100.0% |
| 25% | 100.0% |
| 50% | 98.2% |

## 3. False positives on non-marshalling motion

Nobody is signalling in any of these. Every classification other than `unknown` is wrong by construction, and one that maps to `emergency_stop` fires the CRITICAL master-warning takeover.

| motion | false-signal rate | classified as |
|---|---|---|
| standing | 0% | — |
| waving_hello | 0% | — |
| scratching_head | 0% | — |
| phone_to_ear | 0% | — |
| stretching | 0% | — |
| carrying_box | 0% | — |
| walking_past | 0% | — |
| pointing | 0% | — |
| adjusting_vest | 0% | — |
| checking_watch | 0% | — |
| both_arms_flagging | 100% | `emergency_stop` x40 |
| heaving_load | 0% | — |

**Overall false-signal rate: 8.3%** (40/480); of those, **40** classified as `emergency_stop`.

Temporal gating (`backend/fusion/gating.py`) requires the signal to hold across consecutive windows before it alerts, so an isolated false positive does not reach the operator. These rates are per-window, before gating.

## 4. Confidence calibration

The score is the weakest satisfied condition's margin past its threshold, not a probability. A narrow range for a signal means its rule is only marginally satisfied even by data tuned to it.

| signal | n | min | mean | max |
|---|---|---|---|---|
| move_ahead | 40 | 0.988 | 1.000 | 1.000 |
| turn_left | 40 | 0.984 | 0.997 | 1.000 |
| turn_right | 40 | 0.986 | 0.997 | 1.000 |
| stop | 40 | 1.000 | 1.000 | 1.000 |
| emergency_stop | 40 | 0.643 | 0.681 | 0.683 |
| slow_down | 40 | 0.903 | 0.957 | 1.000 |
| cut_engines | 40 | 0.938 | 0.983 | 1.000 |
| start_engines | 40 | 0.848 | 0.875 | 0.894 |
| chocks_inserted | 40 | 0.602 | 0.633 | 0.684 |
| chocks_removed | 40 | 1.000 | 1.000 | 1.000 |
| all_clear | 40 | 1.000 | 1.000 | 1.000 |
