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
DESTINATION = ["NetLink", "DC_Universe", "Parallel"]

class Fish:
    def __init__(self, name, genesis_pond, remaining_lifetime):
        self.name = name
        self.genesis_pond = genesis_pond
        self.remaining_lifetime = remaining_lifetime
        self.position = (random.randint(0, 550), random.randint(0, 350))  # Initial random position within pond

    def age(self):
        if self.remaining_lifetime > 0:
            self.remaining_lifetime -= 1

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
        self.client.subscribe(f"user/Honey Lemon")

    def on_message(self, client, userdata, msg):
        message = json.loads(msg.payload)
        print(f"Received message: {message}")
        
        if all(key in message for key in ["name", "group_name", "lifetime"]):
            fish = Fish(name=message["name"], genesis_pond=message["group_name"], remaining_lifetime=message["lifetime"])
            self.add_fish(fish)
            
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
        username = random.choice(DESTINATION)
        
        message = {
            "name": fish.name,
            "group_name": fish.genesis_pond,
            "lifetime": fish.remaining_lifetime,
        }
        self.client.publish(f"user/{username}", json.dumps(message))
        print(f"Sending fish to {username}: {message}")
        self.remove_fish(fish)

    def update(self):
        for fish in self.fish_list[:]:
            fish.age()
            if fish.remaining_lifetime <= 0:
                self.remove_fish(fish)
            elif len(self.fish_list) > self.threshold or random.random() < 0.1:
                self.move_fish(fish)

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

        # Fish counter label
        self.fish_counter_label = QLabel(f"Number of Fish: {len(self.pond.fish_list)}")
        self.fish_counter_label.setStyleSheet("font-size: 14px;")
        self.layout.addWidget(self.fish_counter_label)

        # Add Fish button
        self.add_fish_button = QPushButton("Add Fish")
        self.add_fish_button.clicked.connect(self.add_fish)
        self.layout.addWidget(self.add_fish_button)

        # Quit button
        self.quit_button = QPushButton("Quit")
        self.quit_button.clicked.connect(self.quit_application)
        self.layout.addWidget(self.quit_button)

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
        self.update_fish_counter()

    def update_fish_display(self):
        # Clear existing fish labels
        for fish_label in self.fish_labels:
            fish_label.hide()
        self.fish_labels.clear()

        # Add new fish labels
        for fish in self.pond.fish_list:
            movie = QMovie(f"{fish.genesis_pond}.gif")  # Load from local file
            movie.start()

            # Set the scaled size of the GIF to 200x200
            movie.setScaledSize(QSize(200, 200))

            # Set up fish label
            fish_label = QLabel(self.pond_image)
            fish_label.setMovie(movie)

            # Check if the movie's frame is valid
            if movie.frameRect().isNull():
                print("Warning: The movie's frame is null. GIF might not be loaded properly.")

            x, y = fish.position
            fish_label.setGeometry(x, y, 200, 200)  # Set the size of the QLabel to 200x200
            fish_label.show()
            self.fish_labels.append(fish_label)

    def update_pond(self):    
        self.pond.update()
    
        self.update_fish_display()
        self.update_fish_counter()

    def update_fish_counter(self):
        self.fish_counter_label.setText(f"Number of Fish: {len(self.pond.fish_list)}")

    def quit_application(self):
        QApplication.quit()  # Quit the application

if __name__ == "__main__":
    app = QApplication([])
    pond = Pond(POND_NAME)
    ui = PondUI(pond)
    ui.show()
    pond.announce()
    app.exec_()
