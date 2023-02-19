import os
import typing as tp
from collections import defaultdict

import cv2
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from tqdm import tqdm

from config import PathConfig


POINT_EVENTS = ["local_rapid_deprecation", "global_rapid_deprecation"]
STATE_EVENTS = ["global_deviation", "local_deviation", "global_sigmoid_deviation", "local_sigmoid_deviation"]
LABELS = POINT_EVENTS + STATE_EVENTS
POINTS_EVENT_TIME = 1


def load_ground_truth_labels():
    labeled_emotions = defaultdict(list)
    for filename in tqdm(os.listdir(PathConfig.EVAL_VIDEOS_PATH), desc="Loading ground truth labels"):
        filename_split = filename.split('_PLAYER1_')
        file_id = filename_split[0].replace('-zgoda', '')
        emotion, seconds = filename_split[1][:-4].split("_")[1:]
        start_time, end_time = [float(s) for s in seconds.split('-')]
        assert all([len(emotion), isinstance(start_time, float), isinstance(end_time, float)])
        labeled_emotions[file_id].append((emotion, start_time, end_time))
    return dict(labeled_emotions)


def load_info() -> tp.Dict:
    info = {}
    for filename in tqdm(os.listdir(PathConfig.VIDEOS_PATH), desc="Getting videos info"):
        filepath = f"{PathConfig.VIDEOS_PATH}/{filename}"
        filename_split = filename.split("_")
        file_id = f"{filename_split[0]}_{filename_split[1][0]}"
        video = cv2.VideoCapture(filepath)
        fps = video.get(cv2.CAP_PROP_FPS)
        frame_count = video.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps
        info[file_id] = (duration, frame_count, fps)
    return info


def load_predicted_labels():
    def convert_line(_line: str):
        _line_split = _line.split("\t\t")
        return float(_line_split[0]), _line_split[1]

    detections = {}
    for filename in tqdm(os.listdir(PathConfig.OUTPUT_VIDEOS_PATH), desc="Loading predicted labels"):
        with open(f"{PathConfig.OUTPUT_VIDEOS_PATH}/{filename}") as f:
            lines = f.readlines()

        result = []
        for i, line1 in enumerate(lines):
            t0, label1 = convert_line(line1)
            if label1 in POINT_EVENTS:
                result.append((label1, t0, t0 + POINTS_EVENT_TIME))
            if label1 in STATE_EVENTS:
                for line2 in lines[i + 1:]:
                    t1, label2 = convert_line(line2)
                    if label1 == label2:
                        result.append((label1, t0, t1))
                        lines.remove(line2)
                        break

        filename_split = filename.split('_')
        file_id = f"{filename_split[0]}_{filename_split[1][0]}"
        detections[file_id] = result
    return detections


def load_valence_arousal():
    valence, arousal = {}, {}
    for filename in os.listdir(PathConfig.OUTPUT_VA_PATH):
        data = np.loadtxt(f"{PathConfig.OUTPUT_VA_PATH}/{filename}")
        filename = filename.split(".")[0]
        filename_split = filename.split("_")
        file_id = f"{filename_split[0]}_{filename_split[1][0]}"
        if filename.endswith("arousal"):
            arousal[file_id] = data
        elif filename.endswith("valence"):
            valence[file_id] = data
    return valence, arousal


def plot_detections(grand_truth_by_file_id: tp.Dict, info: tp.Dict,
                    predicted: tp.Dict, valence: tp.Dict, arousal: tp.Dict):
    PathConfig.mkdir(PathConfig.PLOTS_PATH)
    for file_id, predictions in tqdm(list(predicted.items()), desc="Plotting..."):
        fig, (a0, a1) = plt.subplots(2, gridspec_kw={'height_ratios': [3, 1]})
        fig.set_size_inches(28.5, 10.5)
        plt.style.use("seaborn-whitegrid")

        video_duration = info[file_id][0]

        # A0
        # Plot label names
        for i, label in enumerate(LABELS):
            a0.text(video_duration / 20, i + 0.5, label, horizontalalignment='center', verticalalignment='center')

        # Plot model predictions boxes
        for label, start_time, end_time in predictions:
            x1 = [start_time, end_time]
            label_index = LABELS.index(label)
            y1 = [label_index + 0.05] * 2
            y2 = [label_index - 0.05 + 1] * 2
            color = cm.get_cmap('winter')((label_index + 1)/len(LABELS))
            a0.fill_between(x1, y1, y2=y2, color=color, label=label)

        # Plot grand truth columns
        bars = []
        for label, start_time, end_time in grand_truth_by_file_id[file_id]:
            intersections_num = sum([x0 < start_time < x1 for (x0, x1) in bars])
            x1 = [start_time, end_time]
            y1 = [0, 0]
            y2 = [len(LABELS) + 0.1 + (intersections_num * 0.3)] * 2
            a0.fill_between(x1, y1, y2=y2, color="red", label=label, alpha=0.1)
            a0.text(np.mean(x1), y2[0] + 0.1, label, horizontalalignment='center', verticalalignment='center')
            bars.append((x1[0] - 20, x1[1] + 20))

        a0.set_xlim(left=0, right=video_duration)
        a0.set_xlabel("Seconds")

        # A1
        # Plot valence and arousal
        a1.plot(valence[file_id], label="Valence")
        a1.plot(arousal[file_id], label="Arousal")

        a1.set_xlim(left=-100, right=len(arousal[file_id]))
        a1.set_xlabel("Frames")
        fig.suptitle(f"Predictions for {file_id}")
        plt.legend()
        plt.savefig(f"{PathConfig.PLOTS_PATH}/{file_id}.png")
        plt.show()


if __name__ == "__main__":
    GRAND_TRUTH = load_ground_truth_labels()
    DF_INFO = load_info()
    PREDICTED = load_predicted_labels()
    VALENCE, AROUSAL = load_valence_arousal()
    plot_detections(GRAND_TRUTH, DF_INFO, PREDICTED, VALENCE, AROUSAL)
