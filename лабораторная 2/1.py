from multiprocessing import Queue

events_queue = Queue()
from dataclasses import dataclass

# формат управляющих команд
@dataclass
class ControlEvent:
    operation: str


@dataclass
class Event:
    source: str  # отправитель
    destination: str  # получатель
    operation: str  # чего хочет (запрашиваемое действие)
    parameters: str  # с какими параметрами

from multiprocessing import Process
from multiprocessing.queues import Empty
from time import sleep


# Класс
class QueueManage(Process):

    def __init__(self, events_q: Queue):
        # вызываем конструктор базового класса
        super().__init__()
        self._events_q = events_q  # очередь событий входящие сообщения
        self._control_q = (
            Queue()
        )  # очередь управляющих команд (например, для остановки)
        self._entity_queues = {}  # словарь очередей известных сущностей
        self._force_quit = False  # флаг завершения работы

    # регистрация очереди новой сущности
    def add_entity_queue(self, entity_id: str, queue: Queue):
        print(f"[ИНФО] регистрируем сущность {entity_id}")
        self._entity_queues[entity_id] = queue

    def _proceed(self, event):
        # print(f'[ИНФО] отправляем запрос {event}')
        try:
            # найдём очередь получателя события
            dst_q: Queue = self._entity_queues[event.destination]
            # и положим запрос в эту очередь
            dst_q.put(event)
        except Exception as e:
            # например, запрос пришёл от или для неизвестной сущности
            print(f"[ИНФО] ошибка выполнения запроса {e}")

    # основной код работы монитора безопасности
    def run(self):
        print("[ИНФО] старт")

        # в цикле проверяет наличие новых событий,
        # выход из цикла по флагу _force_quit
        while self._force_quit is False:
            event = None
            try:
                # ожидание сделано неблокирующим,
                # чтобы можно было завершить работу монитора,
                # не дожидаясь нового сообщения
                event = self._events_q.get_nowait()
                self._proceed(event)
            except Empty:
                # сюда попадаем, если новых сообщений ещё нет,
                # в таком случае немного подождём
                sleep(0.5)
            except Exception as e:
                # что-то пошло не так, выведем сообщение об ошибке
                print(f"[ИНФО] ошибка обработки {e}, {event}")
            self._check_control_q()
        print("[ИНФО] завершение работы")

    # запрос на остановку для завершения работы
    # может вызываться вне процесса
    def stop(self):
        # поскольку работает в отдельном процессе,
        # запрос помещается в очередь, которая проверяется из процесса
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    # проверка наличия новых управляющих команд
    def _check_control_q(self):
        try:
            # проверим, есть ли новые управляющие команды
            request: ControlEvent = self._control_q.get_nowait()
            if request.operation == "stop":
                print("[ИНФО] получен запрос на остановку")
                self._force_quit = True
        except Empty:
            # новых команд нет, продолжим работу
            pass


class Communication(Process):

    def __init__(self, events_queue: Queue):
        # вызываем конструктор базового класса
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()

    # выдаёт собственную очередь для взаимодействия
    def entity_queue(self):
        return self._own_queue

    # основной код сущности
    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        print(f"[{self.__class__.__name__}] отправляем новое задание")

        # Отметили точку в симуляторе и скопировали её
        task = None
        # запрос для сущности ControlSystem с передачей задания
        event = Event(
            source=self.__class__.__name__,
            destination="ControlSystem",
            operation="new_task",
            parameters=task,
        )

        self.events_queue.put(event)

        # Приём информации о ходе выполнения задачи
        sleep(0.5)
        try:
            status_event = self._own_queue.get_nowait()
            if status_event.operation == "status_update":
                print(
                    f"[{self.__class__.__name__}] статус выполнения: {status_event.parameters}"
                )
        except Empty:
            pass

        print(f"[{self.__class__.__name__}] завершение работы")
from src.control_systems_calc import update_speed_and_direction


class ControlSystem(Process):

    def __init__(self, events_queue: Queue):
        # вызываем конструктор базового класса
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = (
            Queue()
        )  # очередь управляющих команд (например, для остановки)
        self._force_quit = False
        self._targets_points = []  # Точки маршрута
        self._current_coordinates = None  # Текущие координаты АРПБТ
        self._current_speed = 30  # Скорость АРПБТ
        self._current_direction = 90  # Базовое направление движения АРПБТ в градусах
        self._obstacle_distances = (
            []
        )  # Массив расстояний до препятствий по 6 направлениям
        self._status = (
            ""  # Статус АРПБТ, находится в движении или достигла точки маршрута
        )
        self._counter = 1  # Счётчик пройденных точек маршрута
        self.max_speed = 30

    # выдаёт собственную очередь для взаимодействия
    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q
    def send_to_drill(self):
        event = None
        event = Event(
            source=self.__class__.__name__,
            destination="Drill",
            operation="drilling",
            parameters=None,
        )
        self.events_queue.put(event)

    def mission_move(self):
        if (
            self._current_coordinates and self._obstacle_distances is not None
        ):  # Проверка первичной инициализации АРПБТ
            if len(self._targets_points) > 0:
                target_point = self._targets_points[0]
                self._current_speed, self._current_direction, self._status = (
                    update_speed_and_direction(
                        (
                            self._current_coordinates["x"],
                            self._current_coordinates["y"],
                        ),
                        (target_point["x"], target_point["y"]),
                        self._current_speed,
                        self._current_direction,
                        self._obstacle_distances,
                        self.max_speed,
                    )
                )

                if self._status == "success":
                    if target_point["type"] == "checkpoint":

                        print(
                            f"[{self.__class__.__name__}] точка бурения {self._counter} достигнута"
                        )
                        data = {"speed": 0, "direction": self._current_direction}
                        event = Event(
                            source=self.__class__.__name__,
                            destination="Servos",
                            operation="set_velocity",
                            parameters=data,
                        )
                        self.events_queue.put(event)

                        self.send_to_drill()

                    self._status = ""
                    self._counter += 1
                    self._targets_points.pop(0)

                data = {
                    "speed": self._current_speed,
                    "direction": self._current_direction,
                }

                # Передача информации о ходе выполнения задачи
                status_event = Event(
                    source=self.__class__.__name__,
                    destination="Communication",
                    operation="status_update",
                    parameters={
                        "speed": self._current_speed,
                        "direction": self._current_direction,
                        "status": self._status,
                        "current_point": self._counter,
                    },
                )
                self.events_queue.put(status_event)

                # print(f'[{self.__class__.__name__}] отправляем сигналы управления приводами движения {json.dumps(data)}')

                event = Event(
                    source=self.__class__.__name__,
                    destination="Servos",
                    operation="set_velocity",
                    parameters=data,
                )
                self.events_queue.put(event)

    # основной код сущности
    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            sleep(0.02)
            event = None
            self._check_event_queue()
            self._check_control_q()
            self.mission_move()

        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        # поскольку работает в отдельном процессе,
        # запрос помещается в очередь, которая проверяется из процесса
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            print(f"[{self.__class__.__name__}] проверяем запрос {request}")
            if isinstance(request, ControlEvent) and request.operation == "stop":
                # поступил запрос на остановку, поднимаем "красный флаг"
                self._force_quit = True
        except Empty:
            # никаких команд не поступило
            pass

    def _check_event_queue(self):
        event = None
        try:
            event: Event = self._own_queue.get_nowait()

            if event.operation == "new_task":
                print(
                    f"[{self.__class__.__name__}] {event.source} прислал новое задание {event.parameters}"
                )
                print(f"[{self.__class__.__name__}] новое задание: {event.parameters}!")
                self._targets_points = event.parameters

            if event.operation == "get_coordinates":
                # print(f"[{self.__class__.__name__}] текущая позиция АРПБТ {event.parameters}")
                self._current_coordinates = event.parameters

            # if event.operation == "get_directions":
            # print(f"[{self.__class__.__name__}] текущие измерения расстояний до препятствий АРПБТ {event.parameters}")
            #    self._obstacle_distances = event.parameters

        except Empty:
            sleep(0.05)
        except Exception as e:
            # что-то пошло не так, выведем сообщение об ошибке
            print(f"[{self.__class__.__name__}] ошибка обработки {e}, {event}") 

import requests
from multiprocessing import Queue, Process


class Navigation(Process):

    def __init__(self, events_queue: Queue):
        # вызываем конструктор базового класса
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    # выдаёт собственную очередь для взаимодействия
    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    # основной код сущности
    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            try:
                sleep(0.05)
                response = requests.get(
                    "http://127.0.0.1:5000/position"
                )  # URL для получения текущих координат
                response.raise_for_status()
                coordinates = response.json()
                # print(f"[{self.__class__.__name__}]Текущая позиция: X={coordinates['x']}, Y={coordinates['y']}")
                event = Event(
                    source=self.__class__.__name__,
                    destination="ControlSystem",
                    operation="get_coordinates",
                    parameters=coordinates,
                )
                self.events_queue.put(event)
                self._check_control_q()
            except requests.exceptions.RequestException as e:
                print(f"[{self.__class__.__name__}]Ошибка запроса: {e}")
                self._check_control_q()

        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        # поскольку работает в отдельном процессе,
        # запрос помещается в очередь, которая проверяется из процесса
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            print(f"[{self.__class__.__name__}] проверяем запрос {request}")
            if isinstance(request, ControlEvent) and request.operation == "stop":
                # поступил запрос на остановку, поднимаем "красный флаг"
                self._force_quit = True
        except Empty:
            # никаких команд не поступило
            pass
from multiprocessing import Queue, Process


class Servos(Process):

    def __init__(self, events_queue: Queue):
        # вызываем конструктор базового класса
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    # выдаёт собственную очередь для взаимодействия
    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    # основной код сущности
    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            self._check_event_queue()
            self._check_control_q()

        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        # поскольку работает в отдельном процессе,
        # запрос помещается в очередь, которая проверяется из процесса
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_event_queue(self):
        event = None
        try:
            event: Event = self._own_queue.get_nowait()
            if event.operation == "set_velocity":
                # print(f"[{self.__class__.__name__}] {event.source} прислал новые параметры управления {event.parameters}")
                try:
                    data = event.parameters
                    response = requests.post(
                        "http://127.0.0.1:5000/set_velocity", json=data
                    )  # URL для обновления параметров скорости и направления движения
                    response.raise_for_status()
                except requests.exceptions.RequestException as e:
                    print(f"[{self.__class__.__name__}]Ошибка запроса: {e}")
        except Empty:
            sleep(0.05)
        except Exception as e:
            # что-то пошло не так, выведем сообщение об ошибке
            print(f"[{self.__class__.__name__}] ошибка обработки {e}, {event}")

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            print(f"[{self.__class__.__name__}] проверяем запрос {request}")
            if isinstance(request, ControlEvent) and request.operation == "stop":
                # поступил запрос на остановку, поднимаем "красный флаг"
                self._force_quit = True
        except Empty:
            # никаких команд не поступило
            pass

# Модуль бурения тоннеля
from multiprocessing import Queue, Process


class Drill(Process):

    def __init__(self, events_queue: Queue):
        # вызываем конструктор базового класса
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    # выдаёт собственную очередь для взаимодействия
    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    # основной код сущности
    def run(self):
        print(f"[{self.__class__.__name__}] старт")

        while self._force_quit is False:
            self._check_event_queue()
            self._check_control_q()

        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        # поскольку работает в отдельном процессе,
        # запрос помещается в очередь, которая проверяется из процесса
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_event_queue(self):
        event = None
        try:
            event: Event = self._own_queue.get_nowait()
            if event.operation == "drilling":
                print(f"[{self.__class__.__name__}] бурим тоннель")
        except Empty:
            sleep(0.1)
        except Exception as e:
            # что-то пошло не так, выведем сообщение об ошибке
            print(f"[{self.__class__.__name__}] ошибка обработки {e}, {event}")

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            print(f"[{self.__class__.__name__}] проверяем запрос {request}")
            if isinstance(request, ControlEvent) and request.operation == "stop":
                # поступил запрос на остановку, поднимаем "красный флаг"
                self._force_quit = True
        except Empty:
            # никаких команд не поступило
            pass

queue_manager = QueueManage(events_queue)
communication = Communication(events_queue)
control_system = ControlSystem(events_queue)
navigation = Navigation(events_queue)
servos = Servos(events_queue)
drill = Drill(events_queue)

queue_manager.add_entity_queue(
    communication.__class__.__name__, communication.entity_queue()
)
queue_manager.add_entity_queue(
    control_system.__class__.__name__, control_system.entity_queue()
)
queue_manager.add_entity_queue(navigation.__class__.__name__, navigation.entity_queue())
queue_manager.add_entity_queue(servos.__class__.__name__, servos.entity_queue())
queue_manager.add_entity_queue(drill.__class__.__name__, drill.entity_queue())


if __name__ == "__main__":
    queue_manager.start()
    communication.start()
    control_system.start()
    navigation.start()
    servos.start()
    drill.start()

    sleep(120)  # можно менять

    queue_manager.stop()
    communication.join()
    control_system.stop()
    navigation.stop()
    servos.stop()
    drill.stop()
