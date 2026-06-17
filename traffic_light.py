from multiprocessing import Queue, Process
from multiprocessing.queues import Empty
from time import sleep
from dataclasses import dataclass
import json

from multiprocessing import Queue
monitor_events_queue = Queue()
from dataclasses import dataclass


@dataclass
class Event:
    source: str       # отправитель
    destination: str  # получатель
    operation: str    # чего хочет (запрашиваемое действие)
    parameters: str   # с какими параметрами

# формат управляющих команд для монитора
@dataclass
class ControlEvent:
    operation: str

# список разрешенных сочетаний сигналов светофора
# любые сочетания, отсутствующие в этом списке, запрещены
traffic_lights_allowed_configurations = [
    {"direction_1": "red", "direction_2": "green"},
    {"direction_1": "red", "direction_2": "red"},    
    {"direction_1": "red", "direction_2": "yellow"},    
    {"direction_1": "yellow", "direction_2": "yellow"},    
    {"direction_1": "off", "direction_2": "off"},
    {"direction_1": "green", "direction_2": "red"},    
    {"direction_1": "green", "direction_2": "yellow"},
    {"direction_1": "yellow_blinking", "direction_2": "yellow_blinking"},
    {"direction_1": "green_left_arrow", "direction_2": "red"},
    {"direction_1": "red", "direction_2": "green_left_arrow"},
]
    
class Monitor(Process):

    def __init__(self, events_q: Queue):
        super().__init__()
        self._events_q = events_q
        self._control_q = Queue()
        self._entity_queues = {}
        self._force_quit = False

    def add_entity_queue(self, entity_id: str, queue: Queue):
        print(f"[монитор] регистрируем сущность {entity_id}")
        self._entity_queues[entity_id] = queue

    def _check_mode(self, mode_str: str) -> bool:
        mode_ok = False
        try:
            mode = json.loads(mode_str)
            print(f"[монитор] проверяем конфигурацию {mode}")
            return mode in traffic_lights_allowed_configurations
        except:
            return False
   
    def _check_policies(self, event):
        print(f'[монитор] обрабатываем событие {event}')

        authorized = False

        if not isinstance(event, Event):
            return False
        if event.source == "SelfDiagnostics" \
                and event.destination == "ControlSystem":
            authorized = True

        if event.source == "ControlSystem" \
                and event.destination == "LightsGPIO" \
                and event.operation == "set_mode" \
                and self._check_mode(event.parameters):
            authorized = True

        if event.source == "LightsGPIO" \
                and event.destination == "SelfDiagnostics":
            authorized = True

        if event.source == "CitySystemConnector" and event.destination == "ControlSystem":
            if event.operation in ["set_timer", "turn_off"]:
                authorized = True


        if event.source == "ControlSystem" and event.destination == "CitySystemConnector":
            if event.operation == "status_report":
                authorized = True
        if authorized is False:
            print("[монитор] событие не разрешено политиками безопасности")
        return authorized

    def _proceed(self, event):
        print(f'[монитор] отправляем запрос {event}')
        try:
            dst_q: Queue = self._entity_queues[event.destination]
            dst_q.put(event)
        except  Exception as e:
            print(f"[монитор] ошибка выполнения запроса {e}")

    def run(self):
        print('[монитор] старт')

        while not self._force_quit:
            try:
                event = self._events_q.get_nowait()
                if self._check_policies(event):
                    self._proceed(event)
            except Empty:
                sleep(0.5)
            except Exception as e:
                print(f"[монитор] ошибка обработки {e}, {event}")
            self._check_control_q()
        print('[монитор] завершение работы')

    def stop(self):
        request = ControlEvent(operation='stop')
        self._control_q.put(request)

    def _check_control_q(self):
        try:
            request: ControlEvent = self._control_q.get_nowait()
            print(f"[монитор] проверяем запрос {request}")
            if isinstance(request, ControlEvent) and request.operation == 'stop':
                self._force_quit = True
        except Empty:
            pass

from multiprocessing import Queue, Process
import json

class ControlSystem(Process):
    def __init__(self, monitor_queue: Queue):
        super().__init__()
        self.monitor_queue = monitor_queue
        self._own_queue = Queue()
        self.mode = {"direction_1": "green", "direction_2": "red"}
        self.durations = {"direction_1": 10, "direction_2": 5}
        self._timer = 0
        self._current_direction = "direction_1"

    def entity_queue(self):
        return self._own_queue

    def run(self):
        print(f"[{self.__class__.__name__}] старт")
        while True:
            try:
                event = self._own_queue.get_nowait()
                if event.operation == "set_timer":
                    params = json.loads(event.parameters)
                    self.durations.update(params)
                    print(f"[ControlSystem] обновлены таймеры: {self.durations}")
                elif event.operation == "turn_off":
                    self.mode = {"direction_1": "off", "direction_2": "off"}
                    print("[ControlSystem] светофор отключен")
            except Empty:
                pass
            self._timer += 1
            if self._timer >= self.durations[self._current_direction]:
                if self._current_direction == "direction_1":
                    self._current_direction = "direction_2"
                    self.mode = {"direction_1": "red", "direction_2": "green"}
                else:
                    self._current_direction = "direction_1"
                    self.mode = {"direction_1": "green", "direction_2": "red"}
                self._timer = 0
                self.monitor_queue.put(Event(
                    source="ControlSystem",
                    destination="LightsGPIO",
                    operation="set_mode",
                    parameters=json.dumps(self.mode)
                ))

            self.monitor_queue.put(Event(
                source="ControlSystem",
                destination="CitySystemConnector",
                operation="status_report",
                parameters=json.dumps(self.mode)
            ))

            sleep(1)

from multiprocessing import Queue, Process
from time import sleep


class LightsGPIO(Process):

    def __init__(self, monitor_queue: Queue):
        super().__init__()
        self.monitor_queue = monitor_queue
        self._own_queue = Queue()

    def entity_queue(self):
        return self._own_queue

    def run(self):        
        print(f'[{self.__class__.__name__}] старт')
        while True:
            try:
                event = self._own_queue.get_nowait()

                if event.operation == "set_mode":
                    print("[LightsGPIO] Новый режим:", event.parameters)

                    status_event = Event(
                        source="LightsGPIO",
                        destination="SelfDiagnostics",
                        operation="status_report",
                        parameters="MODE_APPLIED"
                    )

                    self.monitor_queue.put(status_event)

            except Empty:
                sleep(0.2)
class SelfDiagnostics(Process):

    def __init__(self, monitor_queue: Queue):
        super().__init__()
        self.monitor_queue = monitor_queue
        self._own_queue = Queue()

    def entity_queue(self):
        return self._own_queue

    def run(self):
        print("[SelfDiagnostics] старт")

        while True:
            event = Event(
                source="SelfDiagnostics",
                destination="ControlSystem",
                operation="status",
                parameters="OK"
            )

            self.monitor_queue.put(event)
            sleep(10)


class CitySystemConnector(Process):
    def __init__(self, monitor_queue: Queue):
        super().__init__()
        self.monitor_queue = monitor_queue
        self._own_queue = Queue()

    def entity_queue(self):
        return self._own_queue

    def run(self):
        print("[CitySystemConnector] старт")
        while True:
            try:
                event = self._own_queue.get_nowait()
                print(f"[CitySystemConnector] Получено событие: {event}")
            except Empty:
                sleep(1)

if __name__ == '__main__':
    monitor = Monitor(monitor_events_queue)
    control_system = ControlSystem(monitor_events_queue)
    lights_gpio = LightsGPIO(monitor_events_queue)
    self_diag = SelfDiagnostics(monitor_events_queue)
    city_system_connector = CitySystemConnector(monitor_events_queue)
    monitor.add_entity_queue("ControlSystem", control_system.entity_queue())
    monitor.add_entity_queue("LightsGPIO", lights_gpio.entity_queue())
    monitor.add_entity_queue("SelfDiagnostics", self_diag.entity_queue())
    monitor.add_entity_queue("CitySystemConnector", city_system_connector.entity_queue())

    monitor.start()
    control_system.start()
    lights_gpio.start()
    self_diag.start()
    city_system_connector.start()
    sleep(15)

    monitor.stop()

    control_system.terminate()
    lights_gpio.terminate()
    self_diag.terminate()
    city_system_connector.terminate()

    control_system.join()
    lights_gpio.join()
    self_diag.join()
    city_system_connector.join()