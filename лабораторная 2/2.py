import requests
import json
from time import sleep
from queue import Empty
from dataclasses import dataclass
from multiprocessing import Queue, Process

from src.sensors_calc import calculate_obstacle_distances
from src.control_systems_calc import update_speed_and_direction

events_queue = Queue()

# формат управляющих команд
@dataclass
class ControlEvent:
    operation: str

@dataclass
class Event:
    source: str         # отправитель
    destination: str    # получатель
    operation: str      # чего хочет (запрашиваемое действие)
    parameters: any     # с какими параметрами

# Класс менеджера очередей
class QueueManage(Process):
    def __init__(self, events_q: Queue):
        super().__init__()
        self._events_q = events_q 
        self._control_q = Queue() 
        self._entity_queues = {} 
        self._force_quit = False 

    def add_entity_queue(self, entity_id: str, queue: Queue):
        print(f"[ИНФО] регистрируем сущность {entity_id}")
        self._entity_queues[entity_id] = queue

    def _proceed(self, event):
        try:
            dst_q: Queue = self._entity_queues[event.destination]
            dst_q.put(event)
        except Exception as e:
            print(f"[ИНФО] ошибка выполнения запроса {e}")

    def run(self):
        print("[ИНФО] старт QueueManage")
        while self._force_quit is False:
            event = None
            try:
                event = self._events_q.get_nowait()
                self._proceed(event)
            except Empty:
                sleep(0.05)
            except Exception as e:
                print(f"[ИНФО] ошибка обработки {e}, {event}")
            self._check_control_q()
        print("[ИНФО] завершение работы QueueManage")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if request.operation == "stop":
                print("[ИНФО] получен запрос на остановку")
                self._force_quit = True
        except Empty:
            pass


class Communication(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    def entity_queue(self):
        return self._own_queue
        
    def control_entity_queue(self):
        return self._control_q

    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        print(f"[{self.__class__.__name__}] отправляем новое задание")

        # Маршрут из 7 точек (старт, 5 промежуточных, финиш)
        task = [
            {"x": 20, "y": 20, "type": "waypoint"},
            {"x": 150, "y": 40, "type": "waypoint"},
            {"x": 300, "y": 60, "type": "waypoint"},
            {"x": 450, "y": 55, "type": "waypoint"},
            {"x": 550, "y": 60, "type": "waypoint"},
            {"x": 650, "y": 65, "type": "waypoint"},
            {"x": 691, "y": 68, "type": "checkpoint"}  # конечная точка - бурим
        ]

        event = Event(
            source=self.__class__.__name__,
            destination="ControlSystem",
            operation="new_task",
            parameters=task,
        )
        self.events_queue.put(event)
        
        while not self._force_quit:
            self._check_control_q()
            sleep(0.1)
            
        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if isinstance(request, ControlEvent) and request.operation == "stop":
                self._force_quit = True
        except Empty:
            pass


class Sensors(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False
        self._config = None

    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    def run(self):
        print(f"[{self.__class__.__name__}] старт")

        try:
            response = requests.get("http://127.0.0.1:5000/config")
            response.raise_for_status()
            self._config = response.json()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Ошибка конфига: {e}")

        while self._force_quit is False:
            try:
                sleep(0.02)
                response = requests.get("http://127.0.0.1:5000/position")
                response.raise_for_status()
                coordinates = response.json()

                if self._config:
                    obstacle_distances = calculate_obstacle_distances(
                        coordinates["x"],
                        coordinates["y"],
                        self._config["obstacles"],
                        (self._config["field_width"], self._config["field_height"]),
                        30,
                    )

                    event = Event(
                        source=self.__class__.__name__,
                        destination="ControlSystem",
                        operation="get_directions",
                        parameters=obstacle_distances,
                    )
                    self.events_queue.put(event)
                self._check_control_q()
            except requests.exceptions.RequestException as e:
                self._check_control_q()

        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if isinstance(request, ControlEvent) and request.operation == "stop":
                self._force_quit = True
        except Empty:
            pass


class ControlSystem(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue() 
        self._force_quit = False
        self._targets_points = [] 
        self._current_coordinates = None 
        self._current_speed = 30 
        self._current_direction = 90 
        self._obstacle_distances = [] 
        self._status = "" 
        self._counter = 1 
        self.max_speed = 30

    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q
        
    def send_to_drill(self):
        event = Event(
            source=self.__class__.__name__,
            destination="Drill",
            operation="drilling",
            parameters=None,
        )
        self.events_queue.put(event)

    def mission_move(self):
        if self._current_coordinates and self._obstacle_distances is not None: 
            if len(self._targets_points) > 0:
                target_point = self._targets_points[0]
                self._current_speed, self._current_direction, self._status = (
                    update_speed_and_direction(
                        (self._current_coordinates["x"], self._current_coordinates["y"]),
                        (target_point["x"], target_point["y"]),
                        self._current_speed,
                        self._current_direction,
                        self._obstacle_distances,
                        self.max_speed,
                    )
                )

                if self._status == "success":
                    if target_point["type"] == "checkpoint":
                        print(f"[{self.__class__.__name__}] точка бурения {self._counter} достигнута")
                        data = {"speed": 0, "direction": self._current_direction}
                        event = Event(
                            source=self.__class__.__name__,
                            destination="Servos",
                            operation="set_velocity",
                            parameters=data,
                        )
                        self.events_queue.put(event)
                        self.send_to_drill()

                    elif target_point["type"] == "waypoint":
                        print(f"[{self.__class__.__name__}] точка {self._counter} маршрута движения достигнута")
                        
                    self._status = ""
                    self._counter += 1
                    self._targets_points.pop(0)

                data = {
                    "speed": self._current_speed,
                    "direction": self._current_direction,
                }

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

                event = Event(
                    source=self.__class__.__name__,
                    destination="Servos",
                    operation="set_velocity",
                    parameters=data,
                )
                self.events_queue.put(event)

    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            sleep(0.02)
            self._check_event_queue()
            self._check_control_q()
            self.mission_move()
        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if isinstance(request, ControlEvent) and request.operation == "stop":
                self._force_quit = True
        except Empty:
            pass

    def _check_event_queue(self):
        try:
            event: Event = self._own_queue.get_nowait()

            if event.operation == "new_task":
                print(f"[{self.__class__.__name__}] {event.source} прислал новое задание")
                self._targets_points = event.parameters

            if event.operation == "get_coordinates":
                self._current_coordinates = event.parameters

            if event.operation == "get_directions":
                self._obstacle_distances = event.parameters

        except Empty:
            pass
        except Exception as e:
            pass


class Navigation(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            try:
                sleep(0.05)
                response = requests.get("http://127.0.0.1:5000/position")
                response.raise_for_status()
                coordinates = response.json()
                event = Event(
                    source=self.__class__.__name__,
                    destination="ControlSystem",
                    operation="get_coordinates",
                    parameters=coordinates,
                )
                self.events_queue.put(event)
                self._check_control_q()
            except requests.exceptions.RequestException as e:
                self._check_control_q()
        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if isinstance(request, ControlEvent) and request.operation == "stop":
                self._force_quit = True
        except Empty:
            pass


class Servos(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            self._check_event_queue()
            self._check_control_q()
        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_event_queue(self):
        try:
            event: Event = self._own_queue.get_nowait()
            if event.operation == "set_velocity":
                try:
                    data = event.parameters
                    response = requests.post("http://127.0.0.1:5000/set_velocity", json=data)
                    response.raise_for_status()
                except requests.exceptions.RequestException as e:
                    pass
        except Empty:
            sleep(0.05)
        except Exception as e:
            pass

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if isinstance(request, ControlEvent) and request.operation == "stop":
                self._force_quit = True
        except Empty:
            pass


class Drill(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False

    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while self._force_quit is False:
            self._check_event_queue()
            self._check_control_q()
        print(f"[{self.__class__.__name__}] завершение работы")

    def stop(self):
        request = ControlEvent(operation="stop")
        self._control_q.put(request)

    def _check_event_queue(self):
        try:
            event: Event = self._own_queue.get_nowait()
            if event.operation == "drilling":
                print(f"[{self.__class__.__name__}] бурим тоннель")
        except Empty:
            sleep(0.1)
        except Exception as e:
            pass

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            if isinstance(request, ControlEvent) and request.operation == "stop":
                self._force_quit = True
        except Empty:
            pass


# --- ЗАПУСК ---
queue_manager = QueueManage(events_queue)
communication = Communication(events_queue)
control_system = ControlSystem(events_queue)
navigation = Navigation(events_queue)
servos = Servos(events_queue)
drill = Drill(events_queue)
sensors = Sensors(events_queue)

queue_manager.add_entity_queue(communication.__class__.__name__, communication.entity_queue())
queue_manager.add_entity_queue(control_system.__class__.__name__, control_system.entity_queue())
queue_manager.add_entity_queue(navigation.__class__.__name__, navigation.entity_queue())
queue_manager.add_entity_queue(servos.__class__.__name__, servos.entity_queue())
queue_manager.add_entity_queue(drill.__class__.__name__, drill.entity_queue())
queue_manager.add_entity_queue(sensors.__class__.__name__, sensors.entity_queue())

if __name__ == "__main__":
    queue_manager.start()
    communication.start()
    control_system.start()
    navigation.start()
    servos.start()
    drill.start()
    sensors.start()

    sleep(180)  # Даем 3 минуты на прохождение маршрута

    queue_manager.stop()
    communication.stop()
    control_system.stop()
    navigation.stop()
    servos.stop()
    drill.stop()
    sensors.stop()
