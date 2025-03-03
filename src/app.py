import logging
import os
import typing as tp
from datetime import timedelta

import cv2
import numpy as np

from .analysis import Analyst, Activator, NaiveAnalyst
from .models import CNNModel, RNNModel
from .utils import ValenceArousal
from .vis import VideoHandler, Frame, ValenceArousalSpace
from config import PathConfig, GeneralConfig, FrameConfig, AnalysisConing


class App:
    def __init__(self):
        self.cnn_model = CNNModel(PathConfig.CNN_MODEL_PATH)
        self.rnn_model = RNNModel(PathConfig.RNN_MODEL_PATH)
        self.face_detection_model = cv2.CascadeClassifier(PathConfig.FACE_RECOGNITION_MODEL_PATH)
        self._reset_attributes()

    def _reset_attributes(self):
        self.rnn_analyst = Analyst(AnalysisConing.RNN_STD_SENSITIVITY, AnalysisConing.RNN_MOVING_AVERAGE_WINDOW,
                                   AnalysisConing.RNN_DERIVATIVE_MOVING_AVERAGE_WINDOW)
        self.cnn_analyst = Analyst(AnalysisConing.CNN_STD_SENSITIVITY, AnalysisConing.CNN_MOVING_AVERAGE_WINDOW,
                                   AnalysisConing.CNN_DERIVATIVE_MOVING_AVERAGE_WINDOW)
        self.rnn_naive_analyst = NaiveAnalyst()
        self.cnn_naive_analyst = NaiveAnalyst()
        self._feature_buffer = []
        self._cnn_va = ValenceArousal()
        self._rnn_va = ValenceArousal()
        self._face_position = None

    def videos_inference(self, shift: int = 0):
        for file_name in sorted(os.listdir(PathConfig.VIDEOS_PATH))[shift:]:
            source = os.path.join(PathConfig.VIDEOS_PATH, file_name)
            try:
                self.video_inference(source, vis=False)
            except Exception as e:
                logging.exception(f"Analysis of {source} failed due to some errors")
                self.save_output(source, str(e), "error")

    def video_inference(self, source: tp.Union[str, int], vis: bool = True, save: bool = True):
        self._reset_attributes()
        with VideoHandler(source) as video_handler:
            while (video_frame := video_handler.read_video_frame()) is not None:
                face_img = self._find_face(video_frame)
                prepared_img = self._prepare_img(face_img)
                self._inference_feature_extractor(prepared_img)
                rnn_run = self._inference_rnn_model()
                self._inference_classification_model()
                self.log_predictions(source, video_handler.get_frame_time())

                self.cnn_analyst.add_inference_result(self._cnn_va, video_handler.get_frame_time())
                if rnn_run:
                    self.rnn_analyst.add_inference_result(self._rnn_va, video_handler.get_frame_time())
                self.rnn_naive_analyst.add_inference_result(self._rnn_va, video_handler.get_frame_time())
                self.cnn_naive_analyst.add_inference_result(self._cnn_va, video_handler.get_frame_time())

                if vis:
                    self._visualize(video_frame, prepared_img)

                if self.listen_for_quit_button():
                    break
        if save:
            self.save_output(source, self.events_as_boris_format(self.rnn_analyst.events), "rnn")
            self.save_output(source, self.events_as_boris_format(self.cnn_analyst.events), "cnn")
            self.save_output(source, self.events_as_boris_format(self.rnn_naive_analyst.events), "rnn_naive")
            self.save_output(source, self.events_as_boris_format(self.cnn_naive_analyst.events), "cnn_naive")
            self.save_va(source, self.rnn_analyst.valence, self.rnn_analyst.arousal, "rnn")
            self.save_va(source, self.cnn_analyst.valence, self.cnn_analyst.arousal, "cnn")

    @staticmethod
    def save_output(source: str, _txt: str, label: str):
        output_path = f"{PathConfig.OUTPUT_VIDEOS_PATH}_{label}"
        PathConfig.mkdir(output_path)
        # Get file from path, change existing extension to .json
        output_file = f"{os.path.split(source)[1].split('.')[0] if isinstance(source, str) else source}.txt"
        output_file = f"{output_path}/{output_file}"
        with open(output_file, 'w') as f:
            f.write(_txt)

    @staticmethod
    def save_va(source, valence: np.ndarray, arousal: np.ndarray, label: str):
        output_path = f"{PathConfig.OUTPUT_VA_PATH}_{label}"
        PathConfig.mkdir(output_path)
        output_file = f"{os.path.split(source)[1].split('.')[0] if isinstance(source, str) else source}"
        output_file = f"{output_path}/{output_file}"
        np.savetxt(f"{output_file}_valence.txt", valence, delimiter=',')
        np.savetxt(f"{output_file}_arousal.txt", arousal, delimiter=',')

    def events_as_boris_format(self, intersections: tp.List[tp.Tuple[timedelta, Activator]]):
        boris_format = []
        for _time, activator in intersections:
            boris_time = str(_time.total_seconds()).split('.')
            boris_time = f"{boris_time[0]}.{boris_time[1][:3]}"
            boris_format.append("\t".join([f"{boris_time}", "", f"{activator.name}", "", "foo"]))
        return "\n".join(boris_format)

    def image_inference(self, image_path: str):
        self._reset_attributes()
        img = cv2.imread(image_path)
        prepared_img = self._prepare_img(img)
        self._inference_feature_extractor(prepared_img)
        self._inference_classification_model()
        self.log_predictions(image_path)

    def _find_face(self, frame: np.ndarray) -> np.ndarray:
        """
        Method that finds face in given frame.
        :param frame: whole image
        :return: position of probably face as tuple of x, y, width, height
        """
        def manhattan_dist(_p1: tp.Tuple[int, int], _p2: tp.Tuple[int, int]):
            return abs(_p1[0] - _p2[0]) + abs(_p1[1] - _p2[1])

        def get_middle(_x: tp.List):
            return _x[0] + _x[2] / 2, _x[1] + _x[3] / 2

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        contrast = cv2.equalizeHist(gray)
        faces = self.face_detection_model.detectMultiScale(contrast, 1.3, 5, flags=cv2.CASCADE_SCALE_IMAGE)
        if len(faces) > 0:
            reference_point = get_middle([0, 0, *frame.shape] if self._face_position is None else self._face_position)
            closest = min(faces, key=lambda _f: manhattan_dist(reference_point, (get_middle(_f))))
            self._face_position = closest if self._face_position is None else (9 * closest + self._face_position) // 10

        if self._face_position is not None:
            x, y, w, h = self._face_position
            x1 = max(x - FrameConfig.FACE_FRAME_OFFSET, 0)
            y1 = max(y - FrameConfig.FACE_FRAME_OFFSET, 0)
            x2 = min(x + w + FrameConfig.FACE_FRAME_OFFSET, frame.shape[1])
            y2 = min(y + h + FrameConfig.FACE_FRAME_OFFSET, frame.shape[0])
            return frame[y1: y2, x1: x2]
        else:
            return frame

    def _prepare_img(self, img: np.ndarray) -> np.ndarray:
        resized_img = cv2.resize(img, self.cnn_model.image_shape)
        grayscale_image = cv2.cvtColor(resized_img, cv2.COLOR_BGR2GRAY)
        # contrast_image = cv2.equalizeHist(grayscale_image)
        normalized_img = (grayscale_image / 255.).astype(np.float)
        return np.expand_dims(normalized_img, axis=-1)

    def _inference_feature_extractor(self, img: np.ndarray):
        x = np.expand_dims(img, axis=0)
        features = self.cnn_model.extract_features(x)[0]
        self._feature_buffer.append(features)
        return features

    def _inference_rnn_model(self):
        if len(self._feature_buffer) >= self.rnn_model.window_size:
            window = self._feature_buffer[-self.rnn_model.window_size:]
            x = np.expand_dims(np.asarray(window), axis=0)
            self._rnn_va = ValenceArousal(*self.rnn_model.predict(x)[0])
            self._feature_buffer = window
            return True
        return False

    def _inference_classification_model(self):
        x = np.expand_dims(self._feature_buffer[-1], axis=0)
        self._cnn_va = ValenceArousal(*self.cnn_model.classify_features(x)[0])

    def _visualize(self, video_frame: np.ndarray, inference_input: np.ndarray):
        frame = Frame(FrameConfig.MAIN_FRAME_SIZE, FrameConfig.FRAME_BACKGROUND)
        frame.add(video_frame, (0, 0), (.5, .33))
        frame.add(inference_input * 255, (0, 0))
        frame.add(ValenceArousalSpace.create_chart(self._cnn_va, self._rnn_va), (0., .3), (.5, .33))
        frame.add(self.cnn_analyst.create_deviation_chart(), (.5, 0), (.5, .25))
        frame.add(self.cnn_analyst.create_deprecation_chart(), (.5, .25), (.5, .25))
        try:
            frame.add(self.rnn_analyst.create_va_chart(), (0, .66), (.5, .33))
            frame.add(self.rnn_analyst.create_deviation_chart(), (.5, .5), (.5, .25))
            frame.add(self.rnn_analyst.create_deprecation_chart(), (.5, .75), (.5, .25))
        except (IndexError, ValueError) as e:
            pass
        frame.show()

    def log_predictions(self, source: tp.Union[str, int], _time: timedelta = None):
        print(f"Source: {source} time: {str(_time)[:12].ljust(8, '.').ljust(12, '0') if _time is not None else ''} | "
              f"CNN: {self._cnn_va} "
              f"RNN: {self._rnn_va} "
              f"Detected RNN Troubles: {[f'{ti}: {act.name}' for ti, act in self.rnn_analyst.events]} "
              f"Detected CNN Troubles: {[f'{ti}: {act.name}' for ti, act in self.cnn_analyst.events]} ")

    @staticmethod
    def listen_for_quit_button() -> bool:
        if cv2.waitKey(1) == ord(GeneralConfig.DEFAULT_QUIT_BUTTON):
            return True
        return False
