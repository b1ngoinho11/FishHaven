import json
import time
import random
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget, QPushButton
from PyQt5.QtGui import QPixmap, QMovie
from PyQt5.QtCore import Qt, QTimer, QSize
import paho.mqtt.client as mqtt

POND_NAME = "Honey Lemon"
MQTT_SERVER = "40.90.169.126"
MQTT_PORT = 1883
MQTT_USERNAME = "dc24"
MQTT_PASSWORD = "kmitl-dc24"

class Fish:
    def __init__(self, name, genesis_pond, remaining_lifetime):
        self.name = name
        self.genesis_pond = genesis_pond
        self.postures = None
        self.remaining_lifetime = remaining_lifetime
        self.current_posture = 0
        self.position = (random.randint(0, 550), random.randint(0, 350))  # Initial random position within pond

    def age(self):
        if self.remaining_lifetime > 0:
            self.remaining_lifetime -= 1

    def move(self):
        x, y = self.position
        dx = random.choice([-50, 0, 50])  # Small movement in x direction
        dy = random.choice([-50, 0, 50])  # Small movement in y direction
        new_x = max(0, min(x + dx, 550))  # Ensure fish stays within pond bounds
        new_y = max(0, min(y + dy, 350))  # Ensure fish stays within pond bounds
        self.position = (new_x, new_y)

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
        self.threshold = 5  # Adjustable threshold for crowded pond

    def on_connect(self, client, userdata, flags, rc):
        print(f"Connected to MQTT server with result code {rc}")
        self.client.subscribe(f"fishhaven/stream")
        self.client.subscribe(f"fishhaven/send")

    def on_message(self, client, userdata, msg):
        message = json.loads(msg.payload)
        print(f"Received message: {message}")
        if message["type"] == "hello":
            print(f"Hello message received from {message['sender']} at {message['timestamp']}")
        elif message["type"] == "fish_move":
            self.add_fish(Fish(message["fish_name"], message["group_name"], message["lifetime"]))

    def announce(self):
        message = {
            "type": "hello",
            "sender": self.name,
            "timestamp": int(time.time()),
            "data": {}
        }
        self.client.publish("fishhaven/stream", json.dumps(message))
        print(f"Announced pond existence: {message}")

    def add_fish(self, fish):
        self.fish_list.append(fish)
        print(f"Added fish {fish.name} to pond {self.name}")

    def remove_fish(self, fish):
        self.fish_list.remove(fish)
        print(f"Removed fish {fish.name} from pond {self.name}")

    def move_fish(self, fish):
        message = {
            "type": "fish_move",
            "fish_name": fish.name,
            "group_name": fish.genesis_pond,
            "postures": fish.postures,
            "lifetime": fish.remaining_lifetime,
            "timestamp": int(time.time())
        }
        # self.client.publish("fishhaven/send", json.dumps(message))
        print(f"Fish sent: {message}")
        self.remove_fish(fish)

    def update(self):
        for fish in self.fish_list[:]:
            fish.age()
            if fish.remaining_lifetime <= 0:
                self.remove_fish(fish)
            elif len(self.fish_list) > self.threshold or random.random() < 0.1:
                self.move_fish(fish)
            else:
                fish.move()

class PondUI(QMainWindow):
    def __init__(self, pond):
        super().__init__()
        self.pond = pond
        self.setWindowTitle("Pond Visualization")
        self.setGeometry(100, 100, 600, 400)

        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout()
        central_widget.setLayout(self.layout)

        # Pond image
        self.pond_image = QLabel()
        self.pond_image.setPixmap(QPixmap("pond.png"))  # Replace with your pond image path
        self.pond_image.setScaledContents(True)
        self.layout.addWidget(self.pond_image)

        # Pond name
        self.pond_label = QLabel(f"Pond Name: {self.pond.name}")
        self.pond_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.layout.addWidget(self.pond_label)

        # Add Fish button
        self.add_fish_button = QPushButton("Add Fish")
        self.add_fish_button.clicked.connect(self.add_fish)
        self.layout.addWidget(self.add_fish_button)

        # Fish images
        self.fish_labels = []

        # Timer for updating the pond
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_pond)
        self.timer.start(1000)  # Update every 1 second

    def add_fish(self):
        fish = Fish(f"Fish{len(self.pond.fish_list) + 1}", self.pond.name, 15)
        self.pond.add_fish(fish)
        self.update_fish_display()

    def update_fish_display(self):
        # Clear existing fish labels
        for fish_label in self.fish_labels:
            fish_label.hide()
        self.fish_labels.clear()

        # Add new fish labels
        for fish in self.pond.fish_list:
            fish_label = QLabel(self.pond_image)
            movie = QMovie(fish.genesis_pond)
            movie.start()

            # Get original GIF size
            original_size = movie.frameRect().size()
            width, height = original_size.width(), original_size.height()

            # Compute scaling factor to fit within 200x200 without distortion
            scale_factor = min(200 / width, 200 / height, 1)  # Ensure it does not upscale
            new_width, new_height = int(width * scale_factor), int(height * scale_factor)

            # Apply the scaled size to the movie
            movie.setScaledSize(QSize(new_width, new_height))

            # Set up fish label
            fish_label.setMovie(movie)
            x, y = fish.position
            fish_label.setGeometry(x, y, new_width, new_height)
            fish_label.show()
            self.fish_labels.append(fish_label)


    def update_pond(self):
        self.pond.update()
        self.update_fish_display()

if __name__ == "__main__":
    app = QApplication([])
    pond = Pond(POND_NAME)
    ui = PondUI(pond)
    ui.show()
    pond.announce()
    app.exec_()