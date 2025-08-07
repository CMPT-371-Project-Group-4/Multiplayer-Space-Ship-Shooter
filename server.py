import json
import math
import random
import socket
import threading
import time

# Config
HOST = ''
PORT = 12345
MAX_PLAYERS = 4
MAP_WIDTH, MAP_HEIGHT = 800, 600
FPS_SLEEP = 1/60 # 60 FPS
MAX_HP = 100
HEALTHPACK_HEAL = 30
BULLET_DAMAGE = 10

# Global states
clients = []
bullets = [] # list of {id, x, y, dx, dy, owner}
players = {}  # player_id -> {ready, x, y, hp, alive, angle}
health_packs = {} # id -> {x, y, claimed}
available_ids = list(range(1, MAX_PLAYERS + 1))  # Player IDs from 1 to MAX_PLAYERS
game_started = False

# Locks & counters
healthpack_lock = threading.Lock()
next_bullet_id = 1
next_healthpack_id = 1

# Send a JSON message to all connected clients
def broadcast(message):
  data = (json.dumps(message) + "\n").encode()
  for c in clients:
    try: c.sendall(data)
    except: pass

# Send a JSON message to a specific client
def send_to(client, message):
  try: client.sendall((json.dumps(message) + "\n").encode())
  except: pass

# Mark a player as ready and start game when all are ready
def handle_ready(pid):
  global game_started

  # Randomize spawn position when ready
  x, y = random.randint(50, MAP_WIDTH - 50), random.randint(50, MAP_HEIGHT - 50)
  players[pid] = {"ready": True, "x": x, "y": y, "hp": MAX_HP, "alive": True, "angle": 0.0}
  print(f"[READY] {pid} is ready")

  # If all players are ready, start the countdown
  if len(players) == MAX_PLAYERS and all(p['ready'] for p in players.values()):
    print("[READY] All players are ready. Starting game...")

    # Start countdown
    for count in (3, 2, 1):
      broadcast({"type": "COUNTDOWN", "count": count})
      time.sleep(1)

    # Start the game
    game_started = True
    broadcast({"type": "GAME_START"})

    # Send everyone's initial positions
    for player_id, attr in players.items():
      broadcast({
        "type":      "UPDATE_POS",
        "player_id": player_id,
        "x":         attr["x"],
        "y":         attr["y"],
        "angle":     attr["angle"]
      })

    print("[GAME] Game started!")

# Update and broadcast a player's movement
def handle_move(pid, x, y, angle):
  players[pid].update({"x": x, "y": y, "angle": angle})
  broadcast({"type": "UPDATE_POS", "player_id": pid, "x": x, "y": y, "angle": angle})

# Spawn and broadcast a bullet unless the player is dead
def handle_shoot(pid):
  global next_bullet_id

  if not players[pid]['alive']:
    return
  
  angle = players[pid]['angle']
  x, y = players[pid]['x'], players[pid]['y']
  dx, dy = math.cos(angle) * 10, math.sin(angle) * 10 # Bullet speed

  # Assign bullet ID
  bid = next_bullet_id
  next_bullet_id += 1

  # Create a bullet
  bullet = {"id": bid, "x": x, "y": y, "dx": dx, "dy": dy, "owner": pid}
  bullets.append(bullet)

  # Broadcast shot fired
  broadcast({
    "type": "BULLET_FIRED",
    "id": bid, "owner": pid, "x": x, "y": y, "dx": dx, "dy": dy
  })

# Attempt to lock and consume a health pack
def handle_pickup_request(pid, hid, client):
  global players, health_packs
  granted = False

  # Check if health pack exists and is unclaimed
  with healthpack_lock:
    pack = health_packs.get(hid)
    if pack and not pack['claimed']:
      pack['claimed'] = True
      granted = True
  
  # If granted, heal the player and notify all clients
  if granted:
    # Heal the player
    players[pid]['hp'] = min(MAX_HP, players[pid]['hp'] + HEALTHPACK_HEAL)

    # Broadcast pickup event and HP update
    broadcast({"type": "PICKUP_GRANTED", "player_id": pid, "healthpack_id": hid})
    broadcast({"type": "HP_UPDATE", "player_id": pid, "hp": players[pid]['hp']})

    # Remove the health pack from the server state
    with healthpack_lock:
      if hid in health_packs:
        del health_packs[hid]
    broadcast({"type": "REMOVE_OBJECT", "id": hid})

    print(f"[PICKUP] {pid} picked up health pack {hid}")
  else:
    send_to(client, {"type": "PICKUP_DENIED", "healthpack_id": hid})

# Dispatch an incoming client message
def handle_message(pid, client, raw_line: str):
  try:
    msg = json.loads(raw_line)
  except json.JSONDecodeError:
    print(f"[ERROR] Invalid JSON from {pid}: {raw_line}")
    return
  
  t = msg.get('type')

  match t:
    case 'READY':
      handle_ready(pid)
    case 'MOVE':
      handle_move(pid, msg['x'], msg['y'], msg['angle'])
    case 'SHOOT':
      handle_shoot(pid)
    case 'PICKUP_REQUEST':
      handle_pickup_request(pid, msg['healthpack_id'], client)
    case _:
      print(f"[ERROR] Unknown message type from {pid}: {t}")    

# If one player remains alive, broadcast game over and reset the game
def check_winner():
  alive_players = [p for p in players if players[p]['alive']]
  if len(alive_players) == 1:
    winner = alive_players[0]
    broadcast({"type": "GAME_OVER", "winner": winner})
    print(f"[WINNER] {winner} wins the game!")
    reset_game()
    return winner
  return None

# Wind back all game state to initial conditions
def reset_game():
  global game_started, bullets, health_packs, players, next_healthpack_id, next_bullet_id

  # Mark all players as not ready
  for _, attr in players.items():
    attr['ready'] = False

  # Clear bullet and health pack lists
  bullets.clear()
  health_packs.clear()

  # Reset flags and counters
  game_started = False
  next_healthpack_id = 1
  next_bullet_id = 1

  print("[RESET] Game state reset. Waiting for players to get ready...")

# Client handler function
def handle_client(conn, pid):
  buff = ""
  try:
    while True:
      chunk = conn.recv(1024).decode()
      if not chunk: break
      buff += chunk
      while "\n" in buff:
        line, buff = buff.split("\n", 1)
        if line.strip():
          handle_message(pid, conn, line)
  except Exception as e:
    print(f"[ERROR] {pid}: {e}")
  finally:
    # Cleanup on disconnect
    conn.close()
    clients.remove(conn)

    # Remove player from game state
    was_alive = False
    if game_started and pid in players and players[pid]['alive']:
      was_alive = True
      broadcast({"type": "PLAYER_ELIMINATED", "player_id": pid})

    players.pop(pid, None)

    # Return the slot ID to the pool
    num = int(pid.replace("player", ""))
    available_ids.append(num)
    available_ids.sort()
    print(f"[DISCONNECT] {pid} left the game.")

    # If the game was started and the player was alive, check for winner
    if game_started and was_alive:
      check_winner()

# Spawn health packs randomly (~1% chance per tick, up to 3 total)
def spawn_health_packs():
  global next_healthpack_id, health_packs

  if random.random() < 0.01 and len(health_packs) < 3:
    hid = f"HPACK_{next_healthpack_id:02d}"
    next_healthpack_id += 1

    # Random position within the map bounds
    x = random.randint(50, MAP_WIDTH - 50)
    y = random.randint(50, MAP_HEIGHT - 50)

    # Add health pack to the shared state
    with healthpack_lock:
      health_packs[hid] = {"x": x, "y": y, "claimed": False}

    # Broadcast health pack spawn
    broadcast({"type": "SPAWN_HEALTHPACK", "id": hid, "x": x, "y": y})

    print(f"[SPAWN] Health pack {hid} at ({x}, {y})")

# Remove off-screen bullets
def remove_offscreen_bullets():
  global bullets
  for b in bullets[:]:
    if not (0 <= b['x'] <= MAP_WIDTH and 0 <= b['y'] <= MAP_HEIGHT):
      bullets.remove(b)
      
# Move bullets
def move_bullets():
  global bullets
  for b in bullets[:]:
    b['x'] += b['dx']
    b['y'] += b['dy']

# Handle bullet collisions
def handle_bullet_collisions():
  global bullets, players

  for b in bullets[:]:
    for pid, player in players.items():
      if pid == b['owner'] or not player['alive']:
        continue

      # Calculate distance
      dist = math.hypot(b['x'] - player['x'], b['y'] - player['y'])
      if dist < 20:  # Collision radius
        # Bullet hit the player
        bullets.remove(b)
        player['hp'] -= BULLET_DAMAGE

        print(f"[HIT] {pid} hit by {b['owner']}. HP: {player['hp']}")

        # Broadcast hit and HP update
        broadcast({"type": "HP_UPDATE", "player_id": pid, "hp": player['hp']})

        # Check if player is eliminated
        if player['hp'] <= 0:
          player['alive'] = False
          broadcast({"type": "PLAYER_ELIMINATED", "player_id": pid})

          # Check for winner
          if check_winner() is not None:
            return
        break

# Core game loop
def game_loop():
  global game_started, next_healthpack_id

  while True:
    time.sleep(FPS_SLEEP)
    if not game_started: continue

    # Remove off-screen bullets
    remove_offscreen_bullets()

    # Spawn health packs
    spawn_health_packs()

    # Move bullets
    move_bullets()

    # Handle bullet collisions
    handle_bullet_collisions()

# Accept new client connections and start their handler
def accept_loop(server_socket):
  while True:
    if len(clients) < MAX_PLAYERS and available_ids:
      conn, addr = server_socket.accept()
      clients.append(conn)

      # Assign player ID
      pid = f"player{available_ids.pop(0)}"
      # Send welcome so client knows its ID
      send_to(conn, {"type": "WELCOME", "player_id": pid})

      # Start a new thread for the client
      threading.Thread(target=handle_client, args=(conn, pid), daemon=True).start()

      print(f"[JOIN] {pid} connected from {addr}")
    else:
      time.sleep(0.5)

# Start the server
def start_server():
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.bind((HOST, PORT))
  server.listen()
  print(f"[SERVER] Server listening on {HOST}:{PORT}")

  # Launch accept thread
  threading.Thread(target=accept_loop, args=(server,), daemon=True).start()

  # Start the main game loop
  threading.Thread(target=game_loop, daemon=True).start()

  print("[LOBBY] Lobby ready! Waiting for players...")

  while True:
    time.sleep(1)  # Keep the server running

if __name__ == "__main__":
  start_server()
