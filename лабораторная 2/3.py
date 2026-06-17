import requests
import json
import math
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
            pass

    def run(self):
        print("[ИНФО] старт QueueManage")
        while self._force_quit is False:
            event = None
            try:
                event = self._events_q.get_nowait()
                self._proceed(event)
            except Empty:
                sleep(0.02)
            except Exception as e:
                pass
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

        # Задание отправляем СРАЗУ В SECURITY MODULE
        event = Event(
            source=self.__class__.__name__,
            destination="SecurityModule",
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
            pass

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
        self._current_speed = 50 
        self._current_direction = 90 
        self._obstacle_distances = [] 
        self._status = "" 
        self._counter = 1 
        self.max_speed = 50 

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
                        # ОТПРАВЛЯЕМ КОМАНДУ ЧЕРЕЗ SECURITY MODULE
                        event = Event(
                            source=self.__class__.__name__,
                            destination="SecurityModule",
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

                # ОТПРАВЛЯЕМ КОМАНДУ ЧЕРЕЗ SECURITY MODULE
                event = Event(
                    source=self.__class__.__name__,
                    destination="SecurityModule",
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
                
                # Шлём координаты в ControlSystem (для расчетов) и в SecurityModule (для проверок)
                event_ctrl = Event(
                    source=self.__class__.__name__,
                    destination="ControlSystem",
                    operation="get_coordinates",
                    parameters=coordinates,
                )
                self.events_queue.put(event_ctrl)
                
                event_sec = Event(
                    source=self.__class__.__name__,
                    destination="SecurityModule",
                    operation="get_coordinates",
                    parameters=coordinates,
                )
                self.events_queue.put(event_sec)

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

# НОВАЯ СУЩНОСТЬ: МОДУЛЬ БЕЗОПАСНОСТИ (Кибериммунитет)
class SecurityModule(Process):
    def __init__(self, events_queue: Queue):
        super().__init__()
        self.events_queue = events_queue
        self._own_queue = Queue()
        self._control_q = Queue()
        self._force_quit = False
        self._targets_points = [] 
        self._current_coordinates = None 
        self._current_speed = 0
        self._current_direction = 90
        
    def entity_queue(self):
        return self._own_queue

    def control_entity_queue(self):
        return self._control_q

    def check_mission(self):
        # Если нет целей или координат - ничего не делаем
        if len(self._targets_points) == 0 or self._current_coordinates is None:
            return

        target_point = self._targets_points[0]
        stop_radius = 5

        dx = target_point["x"] - self._current_coordinates["x"]
        dy = target_point["y"] - self._current_coordinates["y"]
        distance = math.hypot(dx, dy)

        # Если достигли точки - удаляем её из списка
        if distance <= stop_radius:
            self._targets_points.pop(0)
            return

        # Истинный угол на цель
        angle = math.degrees(math.atan2(dx, -dy)) % 360
        
        # ПРОЦЕДУРА БЕЗОПАСНОСТИ: ЦБ 2 
        # Если ControlSystem взломан и шлёт неправильный угол, SecurityModule заменяет его на правильный
        safe_direction = self._current_direction
        diff = abs(angle - safe_direction)
        if diff > 180:
            diff = 360 - diff
            
        # Если угол от ControlSystem отличается от нужного больше чем на 45 градусов, значит нас взломали
        if diff > 45:
            safe_direction = angle
            print(f"[SecurityModule] Блокировка вредоносного угла. Установка безопасного: {safe_direction}")

        # ПРОЦЕДУРА БЕЗОПАСНОСТИ: Ограничение максимальной скорости
        safe_speed = self._current_speed
        if safe_speed > 50:
            safe_speed = 50
            print(f"[SecurityModule] Превышение скорости. Ограничение: {safe_speed}")

        data = {
            "speed": safe_speed,
            "direction": safe_direction,
        }

        # Только если всё безопасно, отправляем команду в Servos
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
            self.check_mission()
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
                self._targets_points = event.parameters
                # Пересылаем проверенное задание в ControlSystem
                event_fwd = Event(
                    source=self.__class__.__name__,
                    destination="ControlSystem",
                    operation="new_task",
                    parameters=event.parameters,
                )
                self.events_queue.put(event_fwd)

            if event.operation == "get_coordinates":
                self._current_coordinates = event.parameters
            
            # Получаем потенциально взломанные данные от Control System
            if event.operation == "set_velocity":
                self._current_speed = event.parameters["speed"]
                self._current_direction = event.parameters["direction"]

        except Empty:
            pass
        except Exception as e:
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
                # Теперь мы принимаем команды ТОЛЬКО от SecurityModule!
                if event.source == "SecurityModule":
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
if __name__ == "__main__":
    queue_manager = QueueManage(events_queue)
    communication = Communication(events_queue)
    control_system = ControlSystem(events_queue)
    navigation = Navigation(events_queue)
    servos = Servos(events_queue)
    drill = Drill(events_queue)
    sensors = Sensors(events_queue)
    security_module = SecurityModule(events_queue) # <--- ДОБАВЛЕН SECURITY MODULE

    queue_manager.add_entity_queue(communication.__class__.__name__, communication.entity_queue())
    queue_manager.add_entity_queue(control_system.__class__.__name__, control_system.entity_queue())
    queue_manager.add_entity_queue(navigation.__class__.__name__, navigation.entity_queue())
    queue_manager.add_entity_queue(servos.__class__.__name__, servos.entity_queue())
    queue_manager.add_entity_queue(drill.__class__.__name__, drill.entity_queue())
    queue_manager.add_entity_queue(sensors.__class__.__name__, sensors.entity_queue())
    queue_manager.add_entity_queue(security_module.__class__.__name__, security_module.entity_queue()) # <--- ДОБАВЛЕН SECURITY MODULE

    queue_manager.start()
    communication.start()
    control_system.start()
    navigation.start()
    servos.start()
    drill.start()
    sensors.start()
    security_module.start()

    sleep(180)  

    queue_manager.stop()
    communication.stop()
    control_system.stop()
    navigation.stop()
    servos.stop()
    drill.stop()
    sensors.stop()
    security_module.stop()
