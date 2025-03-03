import itertools
import typing as tp
from collections import defaultdict
from datetime import timedelta

import numpy as np
from matplotlib import pyplot as plt

from config import AnalysisConing
from src.utils import Buffer, ValenceArousal
from .activator import Activator


class Analyst:
    def __init__(self, sensitivity: float, moving_average_size: int, derivative_moving_average_size: int):
        self.sensitivity = sensitivity
        self.moving_average_size = moving_average_size
        self.derivative_moving_average_size = derivative_moving_average_size

        self.__v = []
        self.__a = []
        self._va_buffer = Buffer()
        self._number_of_reads = 0
        self._intersections: tp.Dict[Activator, tp.List[timedelta]] = defaultdict(list)

        # Deviation activators
        self._va_moving_average = Buffer()
        self._last_deviation_read = 0

        # # Local deviation activator
        self._va_mean_local_buffer = Buffer()
        self._va_std_local_buffer = Buffer()

        # # Global deviation activator
        self._va_mean_global_buffer = Buffer()
        self._va_variance_global_buffer = Buffer()
        self._va_std_global_buffer = Buffer()

        # Rapid deprecation activator
        self._derivative_buffer = Buffer()
        self._derivative_moving_average = Buffer()

        # # Local deprecation activator
        self._local_derivative_mean = Buffer()
        self._local_derivative_std = Buffer()

        # # Global deprecation activator
        self._global_derivative_mean = Buffer()
        self._global_derivative_variance = Buffer()
        self._global_derivative_std = Buffer()

    @property
    def events(self) -> tp.List[tp.Tuple[timedelta, Activator]]:
        # Unpack Activator to make list of Activator, time tuple
        return list(itertools.chain(*[[(t, act) for t in times] for act, times in self._intersections.items()]))

    @property
    def valence(self) -> np.ndarray:
        return np.array(self.__v)

    @property
    def arousal(self) -> np.ndarray:
        return np.array(self.__a)

    def add_inference_result(self, valence_arousal: ValenceArousal, _time: timedelta):
        self.__v.append(valence_arousal.valence)
        self.__a.append(valence_arousal.arousal)

        self._va_buffer.append(valence_arousal)
        # Deviation activators
        self._va_moving_average.append(self.va_moving_average(self.moving_average_size))

        # # Local deviation activator
        self._va_mean_local_buffer.append(self._va_buffer.np().mean(axis=0))
        self._va_std_local_buffer.append(self._va_buffer.np().std(axis=0))

        # # Global deviation activator
        self._va_mean_global_buffer.append(self.va_mean(self._va_mean_global_buffer, valence_arousal))
        self._va_variance_global_buffer.append(self.va_variance(
            self._va_variance_global_buffer, self._va_mean_global_buffer, valence_arousal))
        self._va_std_global_buffer.append(self.va_std(self._va_variance_global_buffer))

        # Rapid deprecation activator
        self._derivative_buffer.append(self.derivative())
        self._derivative_moving_average.append(self.derivative_moving_average(self.derivative_moving_average_size))

        # # Local deprecation activator
        self._local_derivative_mean.append(self._derivative_buffer.np().mean(axis=0))
        self._local_derivative_std.append(self._derivative_buffer.np().std(axis=0))

        # # Global deprecation activator
        self._global_derivative_mean.append(self.va_mean(self._global_derivative_mean, self.derivative()))
        self._global_derivative_variance.append(self.va_variance(self._global_derivative_variance, self._global_derivative_mean, self.derivative()))
        self._global_derivative_std.append(self.va_std(self._global_derivative_variance))

        self._number_of_reads += 1
        self.find_intersections(_time)

    def find_intersections(self, _time: timedelta):
        if _time.total_seconds() < AnalysisConing.DELAY:
            return

        def add_intersect(data_line, control_line, activator, check_oddity: bool = False):
            if not check_oddity or len(self._intersections[activator]) % 2 == 1:
                if self.is_intersection(data_line, control_line):
                    self._intersections[activator].append(_time)

        # Data lines
        deviation_line = self._va_moving_average.np()[:, 0]
        derivative_line = self._derivative_moving_average.np()[:, 0]

        # Control lines
        global_v_std = self.deviation_threshold(self._va_std_global_buffer)[:, 0]
        local_v_std = self.deviation_threshold(self._va_std_local_buffer)[:, 0]
        s_global_v_std = self.sigmoid_threshold(self._va_std_global_buffer)[:, 0]
        s_local_v_std = self.sigmoid_threshold(self._va_std_local_buffer)[:, 0]
        global_v_dv_std = self.derivative_threshold(self._global_derivative_std)[:, 0]
        local_v_dv_std = self.derivative_threshold(self._local_derivative_std)[:, 0]

        # Intersections from above
        add_intersect(deviation_line, global_v_std, Activator.global_deviation)
        add_intersect(deviation_line, local_v_std, Activator.local_deviation)
        add_intersect(deviation_line, s_global_v_std, Activator.global_sigmoid_deviation)
        add_intersect(deviation_line, s_local_v_std, Activator.local_sigmoid_deviation)
        add_intersect(derivative_line, global_v_dv_std, Activator.global_rapid_deprecation)
        add_intersect(derivative_line, local_v_dv_std, Activator.local_rapid_deprecation)

        # Intersections from bottom
        add_intersect(global_v_std, deviation_line, Activator.global_deviation, True)
        add_intersect(local_v_std, deviation_line, Activator.local_deviation, True)
        add_intersect(s_global_v_std, deviation_line, Activator.global_sigmoid_deviation, True)
        add_intersect(s_local_v_std, deviation_line, Activator.local_sigmoid_deviation, True)

    def va_moving_average(self, window_size: int):
        return np.mean(self._va_buffer.last(window_size), axis=0)

    def va_mean(self, mean: Buffer, va: ValenceArousal):
        if mean.is_empty():
            return va
        _va_mean: ValenceArousal = mean[-1] * self._number_of_reads
        _va_mean = _va_mean + va
        _va_mean = _va_mean / (self._number_of_reads + 1)
        return _va_mean

    def va_variance(self, variance: Buffer, mean: Buffer, va: ValenceArousal):
        if len(mean) < 2:
            return np.var(self._va_buffer.np(), axis=0)
        # https://math.stackexchange.com/questions/775391
        _va_variance = variance[-1] * self._number_of_reads
        _va_variance = _va_variance + (va - mean[-1]) * (va - mean[-2])
        _va_variance = _va_variance / (self._number_of_reads + 1)
        return _va_variance

    def va_std(self, variance: Buffer):
        return np.sqrt(variance[-1])

    @staticmethod
    def sigmoid(x: np.ndarray):
        return (2 / (1 + np.exp((-x) * 2.2))) - 1

    def deviation_threshold(self, std_buffer: Buffer):
        return self._va_mean_local_buffer.np() - (std_buffer.np() * self.sensitivity)

    def sigmoid_threshold(self, std_buffer: Buffer):
        return self.sigmoid(self._va_mean_local_buffer.np() - (std_buffer.np() * self.sensitivity))

    def derivative(self):
        if len(self._va_buffer) < 2:
            return ValenceArousal(0, 0)
        return self._va_buffer[-1] - self._va_buffer[-2]

    def derivative_moving_average(self, window_size: int):
        return np.mean(self._derivative_buffer.last(window_size), axis=0)

    def derivative_threshold(self, std_buffer: Buffer):
        return self._local_derivative_mean.np() - (std_buffer.np() * self.sensitivity)

    @staticmethod
    def is_intersection(values_1: tp.List, values_2: tp.List) -> bool:
        """
        A function that checks whether the first buffer intersects the second one from above
        :param values_1: first buffered values
        :param values_2: second buffered values
        :return: True is there is an intersection, False otherwise
        """
        try:
            if values_1[-1] < values_2[-1]:
                if values_1[-2] > values_2[-2]:
                    return True
        except IndexError:
            pass
        return False

    # Charts
    def create_va_chart(self):
        return self.__create_analysis_chart([
            (self._va_buffer.np()[:, 1], "Arousal"),
            (self._va_buffer.np()[:, 0], "Valence")
        ], "RNN raw inference result")

    def create_deviation_chart(self):
        return self.__create_analysis_chart([
            (self._va_buffer.np()[:, 0], "Valence"),
            (self._va_moving_average.np()[:, 0], f"{self.moving_average_size} period average"),
            (self.deviation_threshold(self._va_std_local_buffer)[:, 0], "Local threshold"),
            (self.deviation_threshold(self._va_std_global_buffer)[:, 0], "Global threshold"),
            (self.sigmoid_threshold(self._va_std_local_buffer)[:, 0], "Local sigmoid threshold"),
            (self.sigmoid_threshold(self._va_std_global_buffer)[:, 0], "Global sigmoid threshold"),
        ], "Deviation detection")

    def create_deprecation_chart(self):
        return self.__create_analysis_chart([
            (self._derivative_buffer.np()[:, 0], "Derivative value"),
            (self._derivative_moving_average.np()[:, 0], f"{self.derivative_moving_average_size} period average"),
            (self.derivative_threshold(self._local_derivative_std)[:, 0], "Local threshold"),
            (self.derivative_threshold(self._global_derivative_std)[:, 0], "Global threshold"),
        ], "Rapid deprecation detection")

    @staticmethod
    def __create_analysis_chart(data: tp.List[tp.Tuple[tp.List[float], str]], title: str):
        plt.close()
        fig, ax = plt.subplots()
        for values, label in data:
            ax.plot(values, label=label)

        plt.title(title)
        plt.legend()
        fig.canvas.draw()
        return np.array(fig.canvas.renderer.buffer_rgba())[:, :, :3]
