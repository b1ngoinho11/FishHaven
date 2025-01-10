import json
import time
import threading
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget, QPushButton
from PyQt5.QtGui import QPixmap
import paho.mqtt.client as mqtt

POND_NAME = "Honey Lemon"
MQTT_SERVER = "40.90.169.126"
MQTT_PORT = 1883
MQTT_USERNAME = "dc24"
MQTT_PASSWORD = "kmitl-dc24"

class Pond:
    def __init__(self, name):
        self.name = name
        self.fish_list = []
        self.client = mqtt.Client()
        self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(MQTT_SERVER, MQTT_PORT, 60)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        print(f"Connected to MQTT server with result code {rc}")
        self.client.subscribe(f"fishhaven/stream")

    def on_message(self, client, userdata, msg):
        message = json.loads(msg.payload)
        print(f"Received message: {message}")
        if message["type"] == "hello":
            print(f"Hello message received from {message['sender']} at {message['timestamp']}")

    def announce(self):
        message = {
            "type": "hello",
            "sender": self.name,
            "timestamp": int(time.time()),
            "data": {}
        }
        self.client.publish("fishhaven/stream", json.dumps(message))
        print(f"Announced pond existence: {message}")

class PondUI(QMainWindow):
    def __init__(self, pond):
        super().__init__()
        self.pond = pond
        self.setWindowTitle("Pond Visualization")
        self.setGeometry(100, 100, 600, 400)

        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Pond image
        self.pond_image = QLabel()
        pixmap = QPixmap("pond.png")
        self.pond_image.setPixmap(pixmap)
        self.pond_image.setScaledContents(True)
        layout.addWidget(self.pond_image)

        # Pond name
        self.pond_label = QLabel(f"Pond Name: {self.pond.name}")
        self.pond_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self.pond_label)

        # Add Fish button
        self.add_fish_button = QPushButton("Add Fish")
        self.add_fish_button.clicked.connect(self.add_fish)
        layout.addWidget(self.add_fish_button)

    def add_fish(self):
        print("Add Fish button clicked")

    def start(self):
        threading.Thread(target=self.pond.announce).start()

if __name__ == "__main__":
    app = QApplication([])
    pond = Pond(POND_NAME)
    ui = PondUI(pond)
    ui.show()
    pond.announce()
    app.exec_()
