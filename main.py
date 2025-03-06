import json
import time
import random
import sys
import redis
import threading
import uuid
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget, QPushButton, QHBoxLayout, QDialog, QTextEdit
from PyQt5.QtGui import QPixmap, QMovie
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal, QObject
import paho.mqtt.client as mqtt

# Constants
POND_NAME = "Honey Lemon"
MQTT_SERVER = "40.90.169.126" 
MQTT_PORT = 1883
MQTT_USERNAME = "dc24"
MQTT_PASSWORD = "kmitl-dc24"
DESTINATION = ["NetLink", "DC_Universe", "Parallel"]

# Redis configuration
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REPLICA_CHANNEL = "pond_updates"
STATUS_CHANNEL = "replica_status"
MQTT_RELAY_CHANNEL = "mqtt_relay"

class Fish:
    def __init__(self, name, genesis_pond, remaining_lifetime, fish_id=None, position=None):
        self.id = fish_id or str(uuid.uuid4())
        self.name = name
        self.genesis_pond = genesis_pond
        self.remaining_lifetime = remaining_lifetime
        self.position = position or (random.randint(0, 550), random.randint(0, 350))
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "genesis_pond": self.genesis_pond,
            "remaining_lifetime": self.remaining_lifetime,
            "position": self.position
        }
    
    @classmethod
    def from_dict(cls, data):
        fish = cls(
            name=data["name"],
            genesis_pond=data["genesis_pond"],
            remaining_lifetime=data["remaining_lifetime"],
            fish_id=data["id"],
            position=tuple(data["position"])
        )
        return fish
    
    def age(self):
        if self.remaining_lifetime > 0:
            self.remaining_lifetime -= 1
            return True
        return False

class ReplicationSignals(QObject):
    update_received = pyqtSignal(dict)
    status_update = pyqtSignal(dict)
    mqtt_message = pyqtSignal(dict)

class PondReplica:
    def __init__(self, name, replica_id=None):
        # Basic properties
        self.name = name
        self.replica_id = replica_id or str(uuid.uuid4())[:8]
        self.fish_list = []
        self.fish_dict = {}  # For O(1) lookup
        self.threshold = 5
        self.is_primary = False
        self.signals = ReplicationSignals()
        
        # Set up Redis for replication
        self.redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
        self.pubsub = self.redis_client.pubsub()
        self.pubsub.subscribe(REPLICA_CHANNEL, STATUS_CHANNEL, MQTT_RELAY_CHANNEL)
        self.known_replicas = {
            self.replica_id: {
                'last_seen': time.time(),
                'is_primary': False
            }
        }
        
        # MQTT client setup (will only be active for primary)
        self.mqtt_client = None
        
        # Start listeners
        self.replica_thread = threading.Thread(target=self.listen_for_updates)
        self.replica_thread.daemon = True
        self.replica_thread.start()
        
        # Register with the replication system
        self.register_replica()
        
        # Initialize heartbeat
        self.last_heartbeat = time.time()
        self.heartbeat_thread = threading.Thread(target=self.send_heartbeats)
        self.heartbeat_thread.daemon = True
        self.heartbeat_thread.start()
        
        print(f"Replica {self.replica_id} initialized")
        
    def setup_mqtt_client(self):
        """Set up MQTT client only for primary replica"""
        if not self.is_primary:
            return

        # Clean up any existing MQTT client
        if self.mqtt_client:
            try:
                self.mqtt_client.disconnect()
            except:
                pass

        # Create new MQTT client
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(MQTT_SERVER, MQTT_PORT, 60)
        self.mqtt_client.loop_start()
        print(f"MQTT client set up for primary replica {self.replica_id}")

    
    def register_replica(self):
        """Register this replica with the replication system"""
        status_message = {
            "type": "register",
            "replica_id": self.replica_id,
            "timestamp": time.time(),
            "name": self.name,
            "is_primary": self.is_primary
        }
        self.redis_client.publish(STATUS_CHANNEL, json.dumps(status_message))
        # Get existing state if any
        self.request_state_synchronization()
    
    def request_state_synchronization(self):
        """Request state sync from other replicas"""
        sync_request = {
            "type": "sync_request",
            "replica_id": self.replica_id,
            "timestamp": time.time()
        }
        self.redis_client.publish(STATUS_CHANNEL, json.dumps(sync_request))
        
    def send_heartbeats(self):
        """Enhanced heartbeat to include more replica information"""
        while True:
            try:
                # Cleanup stale replicas
                current_time = time.time()
                self.known_replicas = {
                    rid: details for rid, details in self.known_replicas.items()
                    if current_time - details['last_seen'] < 15
                }
                
                heartbeat = {
                    "type": "heartbeat",
                    "replica_id": self.replica_id,
                    "timestamp": time.time(),
                    "is_primary": self.is_primary,
                    "fish_count": len(self.fish_list),
                    "known_replicas": list(self.known_replicas.keys())  # Include known replicas
                }
                self.redis_client.publish(STATUS_CHANNEL, json.dumps(heartbeat))
                time.sleep(2)  # Send heartbeat every 2 seconds
            except Exception as e:
                print(f"Heartbeat error: {e}")
                time.sleep(1)
    
    def send_state(self, target_replica=None):
        """Send complete state to another replica or broadcast"""
        state = {
            "type": "full_state",
            "replica_id": self.replica_id,
            "timestamp": time.time(),
            "fish": [fish.to_dict() for fish in self.fish_list],
            "target_replica": target_replica
        }
        self.redis_client.publish(REPLICA_CHANNEL, json.dumps(state))
    
    def on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection handler for primary replica"""
        if not self.is_primary:
            return
        
        print(f"Connected to MQTT server with result code {rc}")
        self.mqtt_client.subscribe(f"fishhaven/stream")
        self.mqtt_client.subscribe(f"user/{POND_NAME}")

    def on_mqtt_message(self, client, userdata, msg):
        """Handle incoming MQTT messages for primary replica"""
        if not self.is_primary:
            return

        try:
            message = json.loads(msg.payload)
            print(f"Received MQTT message: {message}")
            
            # Relay the message to other replicas via Redis
            relay_message = {
                "type": "mqtt_message",
                "topic": msg.topic,
                "payload": message
            }
            self.redis_client.publish(MQTT_RELAY_CHANNEL, json.dumps(relay_message))
            
            # Handle fish arrival from external source
            if msg.topic == f"user/{POND_NAME}" and all(key in message for key in ["name", "group_name", "lifetime"]):
                fish = Fish(
                    name=message["name"], 
                    genesis_pond=message["group_name"], 
                    remaining_lifetime=message["lifetime"]
                )
                self.add_fish(fish, external=True)
        except Exception as e:
            print(f"Error processing MQTT message: {e}")
    
    def listen_for_updates(self):
        """Listen for updates from other replicas and MQTT relay"""
        try:
            for message in self.pubsub.listen():
                if message['type'] == 'message':
                    channel = message['channel'].decode('utf-8')
                    try:
                        data = json.loads(message['data'].decode('utf-8'))
                    except json.JSONDecodeError:
                        continue
                    
                    if channel == REPLICA_CHANNEL:
                        self.process_replica_update(data)
                    elif channel == STATUS_CHANNEL:
                        self.process_status_update(data)
                    elif channel == MQTT_RELAY_CHANNEL:
                        self.process_mqtt_relay(data)
        except Exception as e:
            print(f"Error in replication listener: {e}")
            # Try to reconnect
            time.sleep(1)
            self.pubsub = self.redis_client.pubsub()
            self.pubsub.subscribe(REPLICA_CHANNEL, STATUS_CHANNEL, MQTT_RELAY_CHANNEL)
            self.listen_for_updates()
    
    def process_replica_update(self, data):
        """Process updates from other replicas"""
        if data["replica_id"] == self.replica_id:
            return  # Ignore our own updates
        
        # Handle targeted messages
        if data.get("target_replica") and data["target_replica"] != self.replica_id:
            return  # This message is not for us
            
        # Process based on update type
        if data["type"] == "add_fish":
            fish_data = data["fish"]
            # Check if we already have this fish
            if fish_data["id"] not in self.fish_dict:
                fish = Fish.from_dict(fish_data)
                self.add_fish(fish, propagate=False)
                
        elif data["type"] == "remove_fish":
            fish_id = data["fish_id"]
            if fish_id in self.fish_dict:
                fish = self.fish_dict[fish_id]
                self.remove_fish(fish, propagate=False)
                
        elif data["type"] == "update_fish":
            fish_data = data["fish"]
            if fish_data["id"] in self.fish_dict:
                # Update existing fish
                fish = self.fish_dict[fish_data["id"]]
                fish.remaining_lifetime = fish_data["remaining_lifetime"]
                fish.position = tuple(fish_data["position"])
                
        elif data["type"] == "full_state":
            # Replace our state with the received state
            self.fish_list = []
            self.fish_dict = {}
            for fish_data in data["fish"]:
                fish = Fish.from_dict(fish_data)
                self.fish_list.append(fish)
                self.fish_dict[fish.id] = fish
        
        # Notify UI
        self.signals.update_received.emit(data)
    
    def process_mqtt_relay(self, data):
        """Process MQTT messages relayed by primary replica"""
        if data.get("type") == "mqtt_message":
            # Emit signal for UI or other components to handle
            self.signals.mqtt_message.emit(data)
            
            # Handle specific message types if needed
            if data.get("topic") == f"user/{POND_NAME}":
                message = data.get("payload", {})
                if all(key in message for key in ["name", "group_name", "lifetime"]):
                    fish = Fish(
                        name=message["name"], 
                        genesis_pond=message["group_name"], 
                        remaining_lifetime=message["lifetime"]
                    )
                    self.add_fish(fish, external=True)
    
    def process_status_update(self, data):
        """Enhanced method to handle primary elections, status updates, and new replica detection"""
        current_time = time.time()
        
        # New Replica Detection
        if data["type"] == "register" or data["type"] == "sync_request":
            # A new replica has registered or requested sync
            new_replica_id = data.get("replica_id")
            
            # Don't respond to our own registration
            if new_replica_id != self.replica_id:
                print(f"New replica detected: {new_replica_id}")
                
                # Add to known replicas list
                self.known_replicas[new_replica_id] = {
                    'last_seen': current_time,
                    'is_primary': data.get("is_primary", False)
                }
                
                # If we're primary or have state data, eagerly send full state to the new replica
                if self.is_primary or len(self.fish_list) > 0:
                    print(f"Sending eager update to new replica {new_replica_id}")
                    # Create state update targeted specifically to the new replica
                    state = {
                        "type": "full_state",
                        "replica_id": self.replica_id,
                        "timestamp": current_time,
                        "fish": [fish.to_dict() for fish in self.fish_list],
                        "target_replica": new_replica_id  # Target specific replica
                    }
                    self.redis_client.publish(REPLICA_CHANNEL, json.dumps(state))
        
        # Rest of the existing process_status_update code...
        # Handle primary reassignment
        if data["type"] == "primary_reassignment":
            old_primary = data.get("old_primary")
            new_primary = data.get("new_primary")
            
            # Update known replicas status
            for replica_id in self.known_replicas:
                # Mark the new replica as primary
                if replica_id == new_primary:
                    self.known_replicas[replica_id]['is_primary'] = True
                # Ensure old primary is not marked as primary
                elif replica_id == old_primary:
                    self.known_replicas[replica_id]['is_primary'] = False
            
            # If we are the new primary replica, set our status
            if new_primary == self.replica_id:
                self.is_primary = True
                print(f"Confirmed as new primary after reassignment")
            elif old_primary == self.replica_id:
                self.is_primary = False
                print(f"Demoted from primary during reassignment")
        
        # Existing primary election and heartbeat logic
        if data["type"] in ["primary_election", "primary_declaration"]:
            # Update replica status based on declaration
            if data.get("replica_id"):
                self.known_replicas[data["replica_id"]] = {
                    'last_seen': current_time,
                    'is_primary': data.get("is_primary", False) 
                                if data["type"] != "primary_election" 
                                else False
                }
            
            # Manage our own primary status
            if data.get("replica_id") != self.replica_id:
                # Demote ourselves if another replica declares primary
                if data.get("is_primary", False):
                    self.is_primary = False
        
        # Update replica last seen timestamp
        if data.get("replica_id"):
            self.known_replicas[data["replica_id"]] = {
                'last_seen': current_time,
                'is_primary': self.known_replicas.get(data["replica_id"], {}).get('is_primary', False)
            }
        
        # Cleanup stale replicas
        self.known_replicas = {
            rid: details for rid, details in self.known_replicas.items() 
            if current_time - details.get('last_seen', 0) < 15
        }
        
        # Notify status update
        self.signals.status_update.emit(data)
    
    def declare_primary(self, force=False):
        """More robust primary declaration with force option"""
        # If not force mode, check for existing active primaries
        current_time = time.time()
        active_primaries = [
            rid for rid, details in self.known_replicas.items() 
            if details.get('is_primary', False) and 
            current_time - details.get('last_seen', 0) < 15 and
            rid != self.replica_id
        ]
        
        # Force mode or no active primaries
        if force or not active_primaries:
            # Prepare a primary election message
            election_token = str(uuid.uuid4())
            primary_declaration = {
                "type": "primary_declaration",
                "replica_id": self.replica_id,
                "timestamp": time.time(),
                "is_primary": True,
                "election_token": election_token
            }
            
            # Broadcast the declaration
            self.redis_client.publish(STATUS_CHANNEL, json.dumps(primary_declaration))
            
            # Set ourselves as primary
            self.is_primary = True
            print(f"Replica {self.replica_id} declared as PRIMARY (Force: {force})")
            
            # Re-register to update status
            self.register_replica()
        else:
            print(f"Cannot declare primary. Active primaries exist: {active_primaries}")
            
        if self.is_primary:
            self.setup_mqtt_client()
    
    def announce(self):
        """Announce pond existence via primary replica's MQTT connection"""
        if not self.is_primary:
            return

        message = {
            "type": "hello",
            "sender": self.name,
            "timestamp": int(time.time()),
            "data": {}
        }
        
        if self.mqtt_client:
            self.mqtt_client.publish("fishhaven/stream", json.dumps(message))
            print(f"Announced pond existence: {message}")

    def add_fish(self, fish, propagate=True, external=False):
        """Add a fish to the pond with immediate eager propagation"""
        if fish.id in self.fish_dict:
            return  # Already have this fish
            
        self.fish_list.append(fish)
        self.fish_dict[fish.id] = fish
        print(f"Added fish {fish.name} to pond {self.name}")
        
        # Always propagate, regardless of primary status
        # This ensures all replicas get updates quickly
        if propagate:
            update = {
                "type": "add_fish",
                "replica_id": self.replica_id,
                "timestamp": time.time(),
                "fish": fish.to_dict(),
                "source": "primary" if self.is_primary else "replica"
            }
            self.redis_client.publish(REPLICA_CHANNEL, json.dumps(update))
            
            # Optional: Confirm update via status channel
            confirmation = {
                "type": "update_confirmation",
                "replica_id": self.replica_id,
                "update_type": "add_fish",
                "fish_id": fish.id,
                "timestamp": time.time()
            }
            self.redis_client.publish(STATUS_CHANNEL, json.dumps(confirmation))

    def remove_fish(self, fish, propagate=True):
        """Remove a fish from the pond with immediate eager propagation"""
        if fish.id not in self.fish_dict:
            return  # Don't have this fish
            
        self.fish_list.remove(fish)
        del self.fish_dict[fish.id]
        print(f"Removed fish {fish.name} from pond {self.name}")
        
        # Always propagate removal
        if propagate:
            update = {
                "type": "remove_fish",
                "replica_id": self.replica_id,
                "timestamp": time.time(),
                "fish_id": fish.id,
                "source": "primary" if self.is_primary else "replica"
            }
            self.redis_client.publish(REPLICA_CHANNEL, json.dumps(update))
            
            # Optional: Confirm update via status channel
            confirmation = {
                "type": "update_confirmation",
                "replica_id": self.replica_id,
                "update_type": "remove_fish",
                "fish_id": fish.id,
                "timestamp": time.time()
            }
            self.redis_client.publish(STATUS_CHANNEL, json.dumps(confirmation))

    def update(self):
        """Update the pond state with eager propagation"""
        # Primary replica handles state updates
        if not self.is_primary:
            return
            
        for fish in self.fish_list[:]:
            # Age fish
            if not fish.age():
                self.remove_fish(fish)
                continue
                    
            # Move fish rules
            if len(self.fish_list) > self.threshold or random.random() < 0.1:
                self.move_fish(fish)
                continue
                    
            # Random position update
            dx, dy = random.randint(-10, 10), random.randint(-10, 10)
            x, y = fish.position
            new_position = (max(0, min(550, x + dx)), max(0, min(350, y + dy)))
            
            # Update fish position
            fish.position = new_position
                
            # Eager propagation of fish update
            update = {
                "type": "update_fish",
                "replica_id": self.replica_id,
                "timestamp": time.time(),
                "fish": fish.to_dict(),
                "update_details": {
                    "position_change": {"old": (x, y), "new": new_position}
                }
            }
                
            self.redis_client.publish(REPLICA_CHANNEL, json.dumps(update))
            
            # Optional: Detailed confirmation
            confirmation = {
                "type": "update_confirmation",
                "replica_id": self.replica_id,
                "update_type": "fish_position",
                "fish_id": fish.id,
                "timestamp": time.time()
            }
            self.redis_client.publish(STATUS_CHANNEL, json.dumps(confirmation))

    def move_fish(self, fish):
        """Move a fish to another pond with robust handling"""
        # If not primary, queue the fish for movement
        if not self.is_primary:
            # Option 1: Add to a movement queue
            # This could be a new attribute in the PondReplica class
            if not hasattr(self, 'fish_movement_queue'):
                self.fish_movement_queue = []
            self.fish_movement_queue.append(fish)
            print(f"Queued fish {fish.name} for movement during non-primary state")
            return

        username = random.choice(DESTINATION)
        
        message = {
            "name": fish.name,
            "group_name": fish.genesis_pond,
            "lifetime": fish.remaining_lifetime,
        }
        
        # Ensure MQTT client exists and is connected
        if not self.mqtt_client:
            self.setup_mqtt_client()
        
        try:
            if self.mqtt_client:
                self.mqtt_client.publish(f"user/{username}", json.dumps(message))
                print(f"Sending fish to {username}: {message}")
                self.remove_fish(fish)
            else:
                print("MQTT client not available for fish movement")
        except Exception as e:
            print(f"Error moving fish: {e}")
            # Optionally, you could add the fish back to the movement queue

    def reassign_primary(self, force_local=False):
        """Enhanced primary reassignment with more robust fallback"""
        # Close existing MQTT connection if it exists
        if self.mqtt_client:
            try:
                self.mqtt_client.disconnect()
                self.mqtt_client.loop_stop()
            except:
                pass
            self.mqtt_client = None
            
        current_time = time.time()
        
        # Refresh known replicas
        active_replicas = [
            rid for rid, details in self.known_replicas.items() 
            if current_time - details.get('last_seen', 0) < 15 and 
            rid != self.replica_id
        ]
        
        # Sort active replicas to choose the lowest ID
        active_replicas.sort()
        
        # If we're the current primary or forcing local declaration
        if self.is_primary or force_local:
            # Demote ourselves first
            self.is_primary = False
            
            # If active replicas exist, choose the lowest ID
            if active_replicas:
                new_primary = active_replicas[0]
                
                # Broadcast reassignment
                reassignment = {
                    "type": "primary_reassignment",
                    "old_primary": self.replica_id,
                    "new_primary": new_primary,
                    "timestamp": current_time
                }
                self.redis_client.publish(STATUS_CHANNEL, json.dumps(reassignment))
                
                print(f"Primary reassigned from {self.replica_id} to {new_primary}")
            else:
                # No other active replicas, force local primary
                print("No active replicas. Force declaring local primary.")
                self.declare_primary(force=True)
        else:
            print("Not responsible for primary reassignment.")
            
        # If new primary, set up MQTT client
        if self.is_primary:
            self.setup_mqtt_client()


class PondUI(QMainWindow):
    def __init__(self, replica, replica_id):
        super().__init__()
        self.replica = replica
        self.replica_id = replica_id
        self.known_replicas = {}
        
        # Connect signals from replica
        self.replica.signals.update_received.connect(self.handle_update)
        self.replica.signals.status_update.connect(self.handle_status_update)
        
        self.setWindowTitle(f"Pond Replica {replica_id}")
        self.setGeometry(100, 100, 600, 500)

        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.layout = QVBoxLayout()
        central_widget.setLayout(self.layout)

        # Pond image
        self.pond_image = QLabel()
        self.pond_image.setPixmap(QPixmap("pond.png"))
        self.pond_image.setScaledContents(True)
        self.layout.addWidget(self.pond_image)

        # Status section
        status_layout = QHBoxLayout()
        
        # Pond name and replica info
        self.status_label = QLabel(f"Pond: {self.replica.name} (Replica {replica_id})")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        status_layout.addWidget(self.status_label)
        
        # Primary indicator
        self.primary_label = QLabel("Role: Replica")
        self.primary_label.setStyleSheet("font-size: 14px;")
        status_layout.addWidget(self.primary_label)
        
        self.layout.addLayout(status_layout)

        # Fish counter label
        self.fish_counter_label = QLabel(f"Number of Fish: {len(self.replica.fish_list)}")
        self.fish_counter_label.setStyleSheet("font-size: 14px;")
        self.layout.addWidget(self.fish_counter_label)
        
        # Replicas status
        self.replicas_label = QLabel("Connected Replicas: None")
        self.replicas_label.setStyleSheet("font-size: 14px;")
        self.layout.addWidget(self.replicas_label)

        # Button layout
        button_layout = QHBoxLayout()
        
        # Add Fish button
        self.add_fish_button = QPushButton("Add Fish")
        self.add_fish_button.clicked.connect(self.add_fish)
        button_layout.addWidget(self.add_fish_button)
        
        # Force Primary button
        self.force_primary_button = QPushButton("Force Primary")
        self.force_primary_button.clicked.connect(self.force_primary)
        button_layout.addWidget(self.force_primary_button)
        
        # Simulate Crash button
        self.crash_button = QPushButton("Simulate Crash")
        self.crash_button.clicked.connect(self.simulate_crash)
        button_layout.addWidget(self.crash_button)
        
        # NEW: Replica Details button
        self.replica_details_button = QPushButton("Replica Details")
        self.replica_details_button.clicked.connect(self.print_replica_details)
        button_layout.addWidget(self.replica_details_button)

        # Quit button
        self.quit_button = QPushButton("Quit")
        self.quit_button.clicked.connect(self.quit_application)
        button_layout.addWidget(self.quit_button)
        
        self.layout.addLayout(button_layout)

        # Fish images
        self.fish_labels = []

        # Timer for updating the pond
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_pond)
        self.timer.start(1000)  # Update every 1 second

    def handle_update(self, data):
        """Handle updates from the replica"""
        self.update_fish_display()
        self.update_fish_counter()
    
    def handle_status_update(self, data):
        """Enhanced status update to reflect primary changes"""
        # Handle primary reassignment specifically
        if data.get("type") == "primary_reassignment":
            old_primary = data.get("old_primary")
            new_primary = data.get("new_primary")
            
            # If we are the new primary or our replica is involved
            if new_primary == self.replica_id or old_primary == self.replica_id:
                # Update primary status
                self.replica.is_primary = (new_primary == self.replica_id)
                
                print(f"UI Updated: Old Primary={old_primary}, New Primary={new_primary}")
        
        # Update known replicas
        current_time = time.time()
        if data.get("replica_id"):
            self.known_replicas[data["replica_id"]] = {
                "last_seen": current_time,
                "is_primary": data.get("is_primary", False)
            }
        
        # Update replicas status display
        active_replicas = [
            rid for rid, info in self.known_replicas.items() 
            if current_time - info["last_seen"] < 10
        ]
        self.replicas_label.setText(f"Connected Replicas: {', '.join(active_replicas)}")
        
        # Explicit primary status update
        if self.replica.is_primary:
            self.primary_label.setText("Role: PRIMARY")
            self.primary_label.setStyleSheet("font-size: 14px; color: green; font-weight: bold;")
        else:
            self.primary_label.setText("Role: Replica")
            self.primary_label.setStyleSheet("font-size: 14px;")

    def add_fish(self):
        """Add a fish to the pond"""
        fish = Fish(f"Fish{random.randint(1000, 9999)}", self.replica.name, 15)
        self.replica.add_fish(fish)
        self.update_fish_display()
        self.update_fish_counter()

    def force_primary(self):
        """Force this replica to become primary and print replica statuses"""
        self.replica.declare_primary()
        self.primary_label.setText("Role: PRIMARY")
        self.primary_label.setStyleSheet("font-size: 14px; color: green; font-weight: bold;")
        
        # Print replica statuses
        print("\n--- Replica Status Report ---")
        current_time = time.time()
        
        # Summarize current known replicas
        print(f"Total Known Replicas: {len(self.replica.known_replicas)}")
        
        for replica_id, replica_info in self.replica.known_replicas.items():
            # Calculate time since last seen
            time_since_seen = current_time - replica_info.get('last_seen', 0)
            
            # Determine status
            status = "Active" if time_since_seen < 15 else "Inactive"
            primary_status = "PRIMARY" if replica_info.get('is_primary', False) else "Replica"
            
            print(f"Replica ID: {replica_id}")
            print(f"  Status: {status}")
            print(f"  Role: {primary_status}")
            print(f"  Last Seen: {time_since_seen:.2f} seconds ago")
            print("---")
        
        print("Forcibly declared this replica as PRIMARY")

    def simulate_crash(self):
        """Simulate a crash and recovery with primary reassignment"""
        # If this is the primary node, attempt to reassign
        if self.replica.is_primary:
            self.replica.reassign_primary()
        
        # Hide UI temporarily
        self.setWindowTitle(f"Pond Replica {self.replica_id} - CRASHED")
        self.status_label.setText(f"Pond: {self.replica.name} (Replica {self.replica_id} - CRASHED)")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: red;")
        
        # Disable updating for a few seconds
        self.timer.stop()
        
        # Clear fish display
        for fish_label in self.fish_labels:
            fish_label.hide()
        self.fish_labels.clear()
        
        # Schedule recovery
        QTimer.singleShot(5000, self.recover_from_crash)
    
    def print_replica_details(self):
        """Print detailed information about known replicas"""
        # Open a dialog to display replica details
        details_dialog = QDialog(self)
        details_dialog.setWindowTitle("Replica Details")
        details_dialog.setGeometry(200, 200, 500, 400)
        
        # Layout for the dialog
        layout = QVBoxLayout()
        details_dialog.setLayout(layout)
        
        # Text area to show replica details
        details_text = QTextEdit()
        details_text.setReadOnly(True)
        layout.addWidget(details_text)
        
        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(details_dialog.close)
        layout.addWidget(close_button)
        
        # Generate detailed replica information
        details = ["--- Replica Status Report ---"]
        details.append(f"Total Known Replicas: {len(self.replica.known_replicas)}")
        details.append(f"Current Replica ID: {self.replica_id}")
        details.append(f"Current Replica Role: {'PRIMARY' if self.replica.is_primary else 'Replica'}\n")
        
        current_time = time.time()
        
        for replica_id, replica_info in self.replica.known_replicas.items():
            # Calculate time since last seen
            time_since_seen = current_time - replica_info.get('last_seen', 0)
            
            # Determine status
            status = "Active" if time_since_seen < 15 else "Inactive"
            primary_status = "PRIMARY" if replica_info.get('is_primary', False) else "Replica"
            
            replica_details = [
                f"Replica ID: {replica_id}",
                f"  Status: {status}",
                f"  Role: {primary_status}",
                f"  Last Seen: {time_since_seen:.2f} seconds ago",
                "---"
            ]
            details.extend(replica_details)
        
        # Set the text in the text area
        details_text.setText("\n".join(details))
        
        # Show the dialog
        details_dialog.exec_()
    
    def recover_from_crash(self):
        """Recover from a simulated crash"""
        self.setWindowTitle(f"Pond Replica {self.replica_id} - RECOVERED")
        self.status_label.setText(f"Pond: {self.replica.name} (Replica {self.replica_id} - RECOVERED)")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: green;")
        
        # Request state sync
        self.replica.request_state_synchronization()
        
        # Reset crash UI after a moment
        QTimer.singleShot(3000, self.reset_crash_ui)
    
    def reset_crash_ui(self):
        """Reset the UI after crash recovery"""
        self.setWindowTitle(f"Pond Replica {self.replica_id}")
        self.status_label.setText(f"Pond: {self.replica.name} (Replica {self.replica_id})")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        
        # Start updating again
        self.timer.start()

    def update_fish_display(self):
        """Update the fish display"""
        # Clear existing fish labels
        for fish_label in self.fish_labels:
            fish_label.hide()
        self.fish_labels.clear()

        # Add new fish labels
        for fish in self.replica.fish_list:
            # Try to use specific pond gif, otherwise use default
            try:
                movie = QMovie(f"{fish.genesis_pond}.gif")
                if not movie.isValid():
                    movie = QMovie("fish.gif")  # Default fish image
            except:
                movie = QMovie("fish.gif")  # Default fish image
                
            movie.start()
            movie.setScaledSize(QSize(50, 50))  # Smaller fish for less clutter

            # Set up fish label
            fish_label = QLabel(self.pond_image)
            fish_label.setMovie(movie)
            
            x, y = fish.position
            fish_label.setGeometry(x, y, 50, 50)
            fish_label.show()
            self.fish_labels.append(fish_label)

    def update_pond(self):
        """Update the pond with strict primary election logic"""
        self.replica.update()
        self.update_fish_display()
        self.update_fish_counter()
        
        # Enhanced primary election logic
        current_time = time.time()
        active_replicas = [
            rid for rid, info in list(self.replica.known_replicas.items()) 
            if current_time - info.get('last_seen', 0) < 15 and rid != self.replica_id
        ]
        
        # Count active primaries
        active_primaries = [
            rid for rid, info in list(self.replica.known_replicas.items())
            if info.get('is_primary', False) and 
            current_time - info.get('last_seen', 0) < 15
        ]
        
        # Determine primary assignment
        if len(active_primaries) > 1:
            # More than one primary - force demotion to lowest ID
            lowest_primary = min(active_primaries)
            if lowest_primary != self.replica_id:
                self.replica.is_primary = False
                print(f"Multiple primaries detected. Demoting to ensure only {lowest_primary} is primary.")
        
        # If no active primary, attempt to become primary
        if len(active_primaries) == 0:
            # Check if we're the lowest ID among active replicas
            if not active_replicas or min(active_replicas + [self.replica_id]) == self.replica_id:
                # Force declare primary if not already
                if not self.replica.is_primary:
                    self.replica.declare_primary(force=True)
                    print(f"Replica {self.replica_id} becoming primary due to no active primary")
        
        # Always update primary label to reflect current state
        if self.replica.is_primary:
            self.primary_label.setText("Role: PRIMARY")
            self.primary_label.setStyleSheet("font-size: 14px; color: green; font-weight: bold;")
        else:
            self.primary_label.setText("Role: Replica")
            self.primary_label.setStyleSheet("font-size: 14px;")
            
    def update_fish_counter(self):
        """Update the fish counter"""
        self.fish_counter_label.setText(f"Number of Fish: {len(self.replica.fish_list)}")

    def quit_application(self):
        """Quit the application with proper primary reassignment"""
        # If this is the primary node, attempt to reassign
        if self.replica.is_primary:
            self.replica.reassign_primary()
        
        # Close the application
        QApplication.quit()

def launch_replica(replica_id):
    """Launch a replica with the given ID"""
    app = QApplication(sys.argv)
    replica = PondReplica(POND_NAME, replica_id)
    ui = PondUI(replica, replica_id)
    ui.show()
    replica.announce()
    sys.exit(app.exec_())

if __name__ == "__main__":
    # Get replica ID from command line or generate one
    replica_id = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())[:8]
    launch_replica(replica_id)