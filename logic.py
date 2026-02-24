import random
import math

class Ball:
    def __init__(self, x, y, radius, color, velocity_x=0, velocity_y=0):
        self.x = x
        self.y = y
        self.radius = radius
        self.color = color  # RGB tuple, e.g., (255, 0, 0)
        self.velocity_x = velocity_x
        self.velocity_y = velocity_y

    def move(self, screen_width, screen_height):
        # Обновляем позицию на основе скорости
        self.x += self.velocity_x
        self.y += self.velocity_y

        # Отражение от стен (простое отражение)
        if self.x - self.radius <= 0 or self.x + self.radius >= screen_width:
            self.velocity_x = -self.velocity_x
        if self.y - self.radius <= 0 or self.y + self.radius >= screen_height:
            self.velocity_y = -self.velocity_y

        # Корректировка позиции, чтобы не выходила за границы
        self.x = max(self.radius, min(self.x, screen_width - self.radius))
        self.y = max(self.radius, min(self.y, screen_height - self.radius))

    def distance_to(self, other_ball):
        return math.sqrt((self.x - other_ball.x)**2 + (self.y - other_ball.y)**2)

    def collides_with(self, other_ball):
        return self.distance_to(other_ball) <= self.radius + other_ball.radius

    def mix_color(self, other_ball):
        # Смешивание цветов: среднее значение RGB
        r = (self.color[0] + other_ball.color[0]) // 2
        g = (self.color[1] + other_ball.color[1]) // 2
        b = (self.color[2] + other_ball.color[2]) // 2
        return (r, g, b)

class Inventory:
    def __init__(self, capacity=10):
        self.balls = []
        self.capacity = capacity

    def add_ball(self, ball):
        if len(self.balls) < self.capacity:
            self.balls.append(ball)
            return True
        return False

    def remove_ball(self, index):
        if 0 <= index < len(self.balls):
            return self.balls.pop(index)
        return None

    def is_empty(self):
        return len(self.balls) == 0

    def get_ball(self, index):
        if 0 <= index < len(self.balls):
            return self.balls[index]
        return None

class GameLogic:
    def __init__(self, screen_width, screen_height, delete_zone_x, delete_zone_y, delete_zone_width, delete_zone_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.balls = []
        self.inventory = Inventory()
        self.delete_zone = {
            'x': delete_zone_x,
            'y': delete_zone_y,
            'width': delete_zone_width,
            'height': delete_zone_height
        }

    def add_ball(self, ball):
        self.balls.append(ball)

    def update(self):
        # Двигаем все шарики
        for ball in self.balls:
            ball.move(self.screen_width, self.screen_height)

        # Проверяем столкновения и смешиваем цвета
        for i in range(len(self.balls)):
            for j in range(i + 1, len(self.balls)):
                if self.balls[i].collides_with(self.balls[j]):
                    # Смешиваем цвета
                    new_color = self.balls[i].mix_color(self.balls[j])
                    self.balls[i].color = new_color
                    self.balls[j].color = new_color

    def suck_ball(self, mouse_x, mouse_y):
        # Находим шарик под мышкой и всасываем в инвентарь
        for ball in self.balls[:]:  # Копируем список для безопасного удаления
            if math.sqrt((ball.x - mouse_x)**2 + (ball.y - mouse_y)**2) <= ball.radius:
                if self.inventory.add_ball(ball):
                    self.balls.remove(ball)
                    return True
        return False

    def spit_ball(self, index, target_x, target_y):
        # Выплёвываем шарик из инвентаря на позицию
        ball = self.inventory.remove_ball(index)
        if ball:
            ball.x = target_x
            ball.y = target_y
            # Случайная скорость
            ball.velocity_x = random.uniform(-2, 2)
            ball.velocity_y = random.uniform(-2, 2)
            self.balls.append(ball)
            return True
        return False

    def delete_ball_in_zone(self):
        # Удаляем шарики в зоне удаления
        to_remove = []
        for ball in self.balls:
            if (self.delete_zone['x'] <= ball.x <= self.delete_zone['x'] + self.delete_zone['width'] and
                self.delete_zone['y'] <= ball.y <= self.delete_zone['y'] + self.delete_zone['height']):
                to_remove.append(ball)
        for ball in to_remove:
            self.balls.remove(ball)
        return len(to_remove)

    def get_balls(self):
        return self.balls

    def get_inventory(self):
        return self.inventory