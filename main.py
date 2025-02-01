import json
import time
import random
import requests
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget, QPushButton
from PyQt5.QtGui import QPixmap, QMovie
from PyQt5.QtCore import Qt, QTimer
import paho.mqtt.client as mqtt

POND_NAME = "Honey Lemon"
MQTT_SERVER = "40.90.169.126"
MQTT_PORT = 1883
MQTT_USERNAME = "dc24"
MQTT_PASSWORD = "kmitl-dc24"
IMG_URL = "https://drive.google.com/file/d/1iASslBb95ngJvUSawuMpq-erXS1j3ZE4/view?usp=drive_link"

class Fish:
    def __init__(self, name, genesis_pond, remaining_lifetime, gif_path):
        self.name = name
        self.genesis_pond = genesis_pond
        self.remaining_lifetime = remaining_lifetime
        self.gif_path = gif_path
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
        if message["type"] == "hello":
            print(f"Hello message received from {message['sender']} at {message['timestamp']}")
        elif message["type"] == "image_sequence":
            self.add_fish(Fish(f"{message['sender']}'s Fish", message['sender'], message['timestamp'], message['data']['gif_path']))

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

    def move_fish(self, fish, gif_path, username):
        message = {
            "type": "image_sequence",
            "sender": fish.genesis_pond,
            "timestamp": fish.remaining_lifetime,
            "data": {
                "gif_path": gif_path
            }
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
                self.move_fish(fish, IMG_URL, "Test")

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
        fish = Fish(f"Fish{len(self.pond.fish_list) + 1}", self.pond.name, 15, IMG_URL)
        self.pond.add_fish(fish)
        self.update_fish_display()

    def update_fish_display(self):
        # Clear existing fish labels
        for fish_label in self.fish_labels:
            fish_label.hide()
        self.fish_labels.clear()

        # Add new fish labels
        for fish in self.pond.fish_list:
            # Download the GIF file locally
            gif_local_path = download_gif(fish.gif_path)

            if gif_local_path:
                movie = QMovie(gif_local_path)  # Load from local file
                movie.start()

                # Set up fish label without scaling
                fish_label = QLabel(self.pond_image)
                fish_label.setMovie(movie)

                # Check if the movie's frame is valid
                if movie.frameRect().isNull():
                    print("Warning: The movie's frame is null. GIF might not be loaded properly.")
                else:
                    print(f"Successfully loaded GIF from {gif_local_path}")

                x, y = fish.position
                fish_label.setGeometry(x, y, movie.frameRect().width(), movie.frameRect().height())
                fish_label.show()
                self.fish_labels.append(fish_label)

    def update_pond(self):
        previous_fish_count = len(self.pond.fish_list)
        
        self.pond.update()
        
        # Only update the display if the number of fish has changed
        if len(self.pond.fish_list) != previous_fish_count:
            self.update_fish_display()

def convert_drive_link_to_direct_url(drive_link):
    # Extract the file ID from the original Google Drive link
    file_id = drive_link.split('/d/')[1].split('/')[0]
    
    # Construct the direct download URL using the file ID
    direct_url = f"https://drive.google.com/uc?id={file_id}"
    
    return direct_url

def download_gif(google_drive_url):
    direct_url = convert_drive_link_to_direct_url(google_drive_url)
    file_id = direct_url.split('=')[-1]
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    local_file = f"/tmp/{file_id}.gif"  # Temporary path for downloaded file

    try:
        # Send GET request to download the file
        response = requests.get(download_url, stream=True)
        response.raise_for_status()

        # Write content to local file
        with open(local_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=128):
                f.write(chunk)
        
        print(f"Downloaded GIF to {local_file}")
        return local_file
    except requests.exceptions.RequestException as e:
        print(f"Error downloading GIF: {e}")
        return None

if __name__ == "__main__":
    app = QApplication([])
    pond = Pond(POND_NAME)
    ui = PondUI(pond)
    ui.show()
    pond.announce()
    app.exec_()
