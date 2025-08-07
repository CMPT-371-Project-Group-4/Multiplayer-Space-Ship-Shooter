import json
import math
import socket
import sys
import threading
import time

import pygame

# Config
WIDTH, HEIGHT = 800, 600
SERVER_HOST = 'localhost'  # Change to your server's IP
SERVER_PORT = 12345
FPS = 60
MOVE_SPEED = 3

# Globals
client_socket = None
my_id = None
players = {} # player_id -> {x, y, hp, alive, angle}
health_packs = {} # id -> (x, y)
bullets = [] # list of {id, x, y, dx, dy, owner}
ready = False
game_started = False
running = True
countdown = None
go_time = None
winner = None

# Pygame setup
pygame.init()
window = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("The Terminator")
clock = pygame.time.Clock()
font_small = pygame.font.SysFont(None, 48)
font_large = pygame.font.SysFont(None, 128)

# Assets
raw_own_sprite = pygame.image.load("assets/ship-self.png").convert_alpha()
own_sprite = pygame.transform.smoothscale(raw_own_sprite, (40, 40))
raw_other_sprite = pygame.image.load("assets/ship-opponent.png").convert_alpha()
other_sprite = pygame.transform.smoothscale(raw_other_sprite, (40, 40))
raw_bg = pygame.image.load("assets/game-background.jpeg").convert()
bg = pygame.transform.smoothscale(raw_bg, (WIDTH, HEIGHT))
raw_hp = pygame.image.load("assets/health-pack.png").convert_alpha()
health_sprite = pygame.transform.smoothscale(raw_hp, (30, 30))

# UI button rects
btn_ready = btn_quit = btn_play_again = btn_exit = None

# Initialize network connection
def init_network():
  global client_socket
  client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  client_socket.connect((SERVER_HOST, SERVER_PORT))
  print("[NETWORK] Connected to server")
  threading.Thread(target=receive_loop, daemon=True).start()

# Send a JSON message to the server
def send(msg):
  try: client_socket.sendall((json.dumps(msg) + "\n").encode())
  except Exception as e:
    print(f"[ERROR] Failed to send message: {e}")

# Reset game state for a new match
def reset_game():
  global players, health_packs, bullets, game_started, ready, countdown, go_time, winner
  players.clear()
  health_packs.clear()
  bullets.clear()
  ready = False
  countdown = None
  go_time = None
  winner = None
  game_started = False

# Handle incoming messages from the server
def handle_message(msg):
  global running, ready, game_started, countdown, go_time, winner, my_id

  match msg.get('type'):
    case "WELCOME":
      my_id = msg['player_id']
      print(f"[WELCOME] You are player {my_id}")

    case "COUNTDOWN":
      countdown = str(msg['count'])

    case "GAME_START":
      countdown = "GO!"
      go_time = time.time()
      game_started = True

    case "UPDATE_POS":
      pid = msg['player_id']
      players[pid] = {
        "x": msg['x'],
        "y": msg['y'],
        "hp": players.get(pid, {}).get('hp', 100),
        "alive": players.get(pid, {}).get('alive', True),
        "angle": msg['angle']
      }

    case "SPAWN_HEALTHPACK":
      health_packs[msg['id']] = (msg['x'], msg['y'])

    case "REMOVE_OBJECT":
      health_packs.pop(msg['id'], None)

    case "PICKUP_DENIED":
      print(f"[PICKUP DENIED] Health pack {msg['healthpack_id']}")

    case "HP_UPDATE":
      pid = msg['player_id']
      if pid in players:
        players[pid]['hp'] = msg['hp']
    
    case "BULLET_FIRED":
      bullets.append({
        "id": msg['id'],
        "x": msg['x'], "y": msg['y'],
        "dx": msg['dx'], "dy": msg['dy'],
        "owner": msg['owner']
      })

    case "REMOVE_BULLET":
      bullets[:] = [b for b in bullets if b['id'] != msg['id']]

    case "PLAYER_ELIMINATED":
      pid = msg['player_id']
      if pid in players:
        players[pid]['alive'] = False

    case "GAME_OVER":
      winner = msg['winner']

    case _:
      pass  # Ignore unknown messages

# Receive loop to handle incoming messages
def receive_loop():
  global running

  buffer = ""
  while running:
    try:
      chunk = client_socket.recv(1024).decode()
      if not chunk: break
      buffer += chunk
      while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        if line.strip():
          try:
            handle_message(json.loads(line))
          except json.JSONDecodeError:
            print(f"[ERROR] Invalid JSON: {line}")
    except OSError as e:
      break
    except Exception as e:
      print(f"Error receiving data: {e}")
      running = False
      break

# Draw the countdown text when game is starting
def draw_countdown():
  global countdown
  text = font_large.render(countdown, True, (255, 255, 255))  # white text
  rect = text.get_rect(center=(WIDTH // 2, HEIGHT // 2))
  window.blit(text, rect)
  if countdown == "GO!" and go_time and time.time() - go_time > 1:
    countdown = None  # Clear countdown after 1 second

# Draw the lobby screen with player ready status and action buttons
def draw_lobby():
  global btn_ready, btn_quit

  # READY/WAITING button
  text1 = font_small.render("READY" if not ready else "WAITING...", True, (0, 255, 0))  # green text
  btn_ready = text1.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 50))
  window.blit(text1, btn_ready)

  # QUIT button
  text2 = font_small.render("QUIT", True, (255, 0, 0))  # red text
  btn_quit = text2.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 50))
  window.blit(text2, btn_quit)

# Draw the game over screen with winner info and action buttons - play again or quit
def draw_game_over():
  global btn_play_again, btn_exit

  # Display winner text
  text1 = font_large.render(f"{winner} WINS!", True, (255, 255, 0))  # yellow text
  rect = text1.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 50))
  window.blit(text1, rect)

  # PLAY AGAIN button
  text2 = font_small.render("PLAY AGAIN", True, (0, 255, 0))  # green text
  btn_play_again = text2.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 20))
  window.blit(text2, btn_play_again)

  # QUIT button
  text3 = font_small.render("QUIT", True, (255, 0, 0))  # red text
  btn_exit = text3.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 80))
  window.blit(text3, btn_exit)

# Update player position and angle based on input
# and send MOVE message to server
def update_player():
  # Don't send updates if not in game or dead
  if not game_started or my_id not in players or not players[my_id]['alive']:
    return
  
  p = players[my_id]
  keys = pygame.key.get_pressed()

  # Update position based on WASD keys
  p['x'] += (keys[pygame.K_d] - keys[pygame.K_a]) * MOVE_SPEED
  p['y'] += (keys[pygame.K_s] - keys[pygame.K_w]) * MOVE_SPEED
  p['x'] = max(0, min(WIDTH, p['x']))
  p['y'] = max(0, min(HEIGHT, p['y']))

  # Update angle based on mouse position
  mouse_x, mouse_y = pygame.mouse.get_pos()
  p['angle'] = math.atan2(mouse_y - p['y'], mouse_x - p['x'])

  # Send MOVE message with updated position and angle
  send({"type": "MOVE", "x": p['x'], "y": p['y'], "angle": p['angle']})

  # Auto-pickup health packs if over one
  px, py = p['x'], p['y']
  for pack_id, (hx, hy) in list(health_packs.items()):
    dist = math.hypot(px - hx, py - hy)
    if dist < 20:  # close enough to pick up
      send({"type": "PICKUP_REQUEST", "healthpack_id": pack_id})
      break  # only pickup one per frame

# Handle mouse events for shooting and UI
def handle_events():
  global running, ready

  for e in pygame.event.get():
    if e.type == pygame.QUIT:
      running = False
    elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
      # Lobby buttons
      if not game_started and winner is None:
        if btn_ready.collidepoint(e.pos) and not ready:
          ready = True
          send({"type": "READY"})
        elif btn_quit.collidepoint(e.pos):
          running = False

      # Game Over buttons
      elif winner:
        if btn_play_again.collidepoint(e.pos):
          reset_game()
        elif btn_exit.collidepoint(e.pos):
          running = False

      # In-game shooting
      elif my_id in players and players[my_id]['alive']:
        send({"type": "SHOOT"})

# Draw the game state on the screen
def draw_in_game():
  # Health packs
  for (x, y) in health_packs.values():
    rect = health_sprite.get_rect(center=(x, y))
    window.blit(health_sprite, rect)

  # Bullets
  for b in bullets:
    b['x'] += b['dx']
    b['y'] += b['dy']

    # Remove bullets that go off-screen
    if not (0 <= b['x'] <= WIDTH and 0 <= b['y'] <= HEIGHT):
      bullets.remove(b)
      continue

    # Remove bullets that hit players
    for pid, player in players.items():
      if pid == b['owner'] or not player['alive']:
        continue
      dist = math.hypot(b['x'] - player['x'], b['y'] - player['y'])
      if dist < 20:  # collision radius
        bullets.remove(b)
        continue

    pygame.draw.circle(window, (255, 255, 0), (int(b['x']), int(b['y'])), 4)  # yellow bullet

  # Players
  for pid, player in players.items():
    if not player['alive']:
      continue

    # Draw player sprite
    surf = own_sprite if pid == my_id else other_sprite
    rotated = pygame.transform.rotate(surf, -math.degrees(player['angle']) - 90)
    rect = rotated.get_rect(center=(player['x'], player['y']))
    window.blit(rotated, rect)

    # HP bar
    bar_w, bar_h = 40, 5
    bx, by = player['x'] - bar_w // 2, player['y'] - 25
    pygame.draw.rect(window, (100, 100, 100), (bx, by, bar_w, bar_h)) # grey background
    fill_w = int(bar_w * (player['hp'] / 100))
    pygame.draw.rect(window, (255, 0, 0), (bx, by, fill_w, bar_h)) # red fill

# Main game loop
# Handles events, updates game state, and draws everything
def main():
  global running, ready, game_started, countdown, go_time, winner, my_id

  init_network()

  # Game loop
  while running:
    clock.tick(FPS) 
    window.blit(bg, (0, 0))

    if countdown:
      draw_countdown()
    elif not game_started and winner is None:
      draw_lobby()
    elif winner:
      draw_game_over()
    else:
      update_player()
      draw_in_game()

    pygame.display.flip()
    handle_events()

  # Clean up
  try:
    client_socket.shutdown(socket.SHUT_RDWR)
  except: pass
  client_socket.close()
  pygame.quit()
  sys.exit()

if __name__ == "__main__":
  main()