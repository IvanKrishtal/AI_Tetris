# Tetris AI — эвристический агент с перебором на 2 фигуры вперёд и
# стратегией "охоты за тетрисами" (один колодец под I-фигуру).
# Структура файла: 
# 1) константы
# 2) агент (оценка поля + выбор хода)
# 3) сухой тетрис (движок игры, отрисовка, игровой цикл)

import pygame
import sys
import random

# ============================ КОНСТАНТЫ ============================

#  окно и поле 
WIDTH, HEIGHT = 600, 700
GRID_SIZE = 30
GRID_WIDTH = 10
GRID_HEIGHT = 20

BLACK = (20, 20, 30)
WHITE = (220, 220, 220)
GRAY = (60, 60, 80)
DARK_GRAY = (40, 40, 55)
RED = (220, 60, 60)

grid_left = 40
grid_top = 40
grid_width_px = GRID_WIDTH * GRID_SIZE
grid_height_px = GRID_HEIGHT * GRID_SIZE

#  фигуры (форма + цвет) 
SHAPES = {
    "I": ([[1, 1, 1, 1]], (0, 240, 240)),
    "O": ([[1, 1], [1, 1]], (240, 240, 0)),
    "T": ([[0, 1, 0], [1, 1, 1]], (160, 0, 240)),
    "S": ([[0, 1, 1], [1, 1, 0]], (0, 240, 0)),
    "Z": ([[1, 1, 0], [0, 1, 1]], (240, 0, 0)),
    "J": ([[1, 0, 0], [1, 1, 1]], (0, 0, 240)),
    "L": ([[0, 0, 1], [1, 1, 1]], (240, 160, 0)),
}

#  веса базовой эвристики (высота, линии, дыры, неровность) 
W_HEIGHT = -0.510066
W_LINES = 0.760666
W_HOLES = -0.75  
W_BUMPINESS = -0.184483
WEIGHTS = (W_HEIGHT, W_LINES, W_HOLES, W_BUMPINESS)

#  стратегия "один колодец под I-фигуру" 
WELL_COLUMN = GRID_WIDTH - 1  # правая колонка — колодец
W_WELL_READY = 1.2  # награда за готовые к тетрису нижние ряды
W_WELL_FILL = -2.0  # штраф за заполнение колодца не тетрисом

#  штраф за "второй колодец" (яма 3+ не в целевой колонке) 
EXTRA_WELL_THRESHOLD = 3
W_EXTRA_WELL = -2.0

#  штраф за длинную закрытую дырку (3+ клетки подряд под потолком) 
HOLE_RUN_THRESHOLD = 3
W_HOLE_RUN = -1.0

#  нелинейный бонус за очистку линий: тетрис несоразмерно выгоднее 
TETRIS_LINE_BONUS = {0: 0.0, 1: 0.3, 2: 0.8, 3: 2.0, 4: 15.0}

#  превью следующей фигуры 
PREVIEW_CELL = 24
PREVIEW_COLS = 4
PREVIEW_ROWS = 3

#  скорость "рук" агента: кадров между шагами (поворот/сдвиг/падение) 
MOVE_DELAY = 4

# очки за очистку линий (обычный тетрисный счёт)
LINE_SCORES = {1: 100, 2: 300, 3: 700, 4: 1500}


# ============================= АГЕНТ =============================
# Оценка поля признаками + перебор всех размещений на 2 фигуры вперёд.


def column_heights(grid):
    # высота "стопки" в каждом столбце (от верхней заполненной клетки до пола)
    heights = [0] * GRID_WIDTH
    for col in range(GRID_WIDTH):
        for row in range(GRID_HEIGHT):
            if grid[row][col]:
                heights[col] = GRID_HEIGHT - row
                break
    return heights


def count_holes(grid, heights):
    # пустые клетки под потолком в каждом столбце
    holes = 0
    for col in range(GRID_WIDTH):
        col_top = GRID_HEIGHT - heights[col]
        for row in range(col_top + 1, GRID_HEIGHT):
            if grid[row][col] == 0:
                holes += 1
    return holes


def bumpiness_excluding_well(heights, well_col=WELL_COLUMN):
    # неровность только по основному стакану — колодец не считаем
    main_heights = [h for i, h in enumerate(heights) if i != well_col]
    return sum(
        abs(main_heights[i] - main_heights[i + 1]) for i in range(len(main_heights) - 1)
    )


def well_readiness(grid, well_col=WELL_COLUMN):
    # сколько подряд нижних строк уже готовы к тетрису (пусто только в колодце)
    count = 0
    for row in range(GRID_HEIGHT - 1, -1, -1):
        row_cells = grid[row]
        well_empty = row_cells[well_col] == 0
        rest_filled = all(row_cells[c] != 0 for c in range(GRID_WIDTH) if c != well_col)
        if well_empty and rest_filled:
            count += 1
            if count >= 4:
                break
        else:
            break
    return count


def extra_well_badness(heights, well_col=WELL_COLUMN, threshold=EXTRA_WELL_THRESHOLD):
    # ищет "лишние колодцы" — столбцы глубже обоих соседей на threshold+
    main_idx = [i for i in range(GRID_WIDTH) if i != well_col]
    n = len(main_idx)
    badness = 0
    for pos, col in enumerate(main_idx):
        left = heights[main_idx[pos - 1]] if pos > 0 else heights[col]
        right = heights[main_idx[pos + 1]] if pos < n - 1 else heights[col]
        depth = min(left, right) - heights[col]
        if depth >= threshold:
            badness += depth**2
    return badness


def covered_hole_runs_badness(grid, heights, threshold=HOLE_RUN_THRESHOLD):
    # ищет длинные серии закрытых дырок подряд в одном столбце
    badness = 0
    for col in range(GRID_WIDTH):
        top = GRID_HEIGHT - heights[col]
        run = 0
        for row in range(top + 1, GRID_HEIGHT):
            if grid[row][col] == 0:
                run += 1
            else:
                if run >= threshold:
                    badness += run**2
                run = 0
        if run >= threshold:
            badness += run**2
    return badness


def evaluate_grid(grid, lines_cleared, weights=WEIGHTS):
    # итоговый скор состояния поля после хода
    heights = column_heights(grid)
    agg_height = sum(heights)
    holes = count_holes(grid, heights)
    bump = bumpiness_excluding_well(heights)
    w_h, w_l, w_o, w_b = weights

    line_bonus = TETRIS_LINE_BONUS.get(lines_cleared, TETRIS_LINE_BONUS[4])
    ready = well_readiness(grid)
    well_height = heights[WELL_COLUMN]
    extra_well = extra_well_badness(heights)
    hole_run = covered_hole_runs_badness(grid, heights)

    return (
        w_h * agg_height
        + w_l * line_bonus
        + w_o * holes
        + w_b * bump
        + W_WELL_READY * (ready**2)
        + W_WELL_FILL * well_height
        + W_EXTRA_WELL * extra_well
        + W_HOLE_RUN * hole_run
    )


def get_all_placements(grid, shape, weights=WEIGHTS):
    # все допустимые размещения фигуры: (поворот, x, поле_после_хода, скор)
    results = []
    for r_idx, rot in enumerate(get_all_rotations(shape)):
        width = len(rot[0])
        for x in range(GRID_WIDTH - width + 1):
            y = drop_y(grid, rot, x)
            if y is None:
                continue
            placed = place_on_grid(grid, rot, x, y, value=1)
            cleared_grid, lines_cleared = clear_full_lines(placed)
            score = evaluate_grid(cleared_grid, lines_cleared, weights)
            results.append((r_idx, x, cleared_grid, score))
    return results


def best_placement_two_step(grid, shape, next_shape, weights=WEIGHTS):
    # выбор хода текущей фигуры с учётом лучшего ответа на следующую
    placements = get_all_placements(grid, shape, weights)
    if not placements:
        return None

    best_total = None
    best = None
    for r_idx, x, resulting_grid, score1 in placements:
        next_placements = get_all_placements(resulting_grid, next_shape, weights)
        best_score2 = max((p[3] for p in next_placements), default=-1e9)
        total = score1 + best_score2
        if best_total is None or total > best_total:
            best_total = total
            best = (r_idx, x)
    return best


# ========================== СУХОЙ ТЕТРИС ==========================
# Движок игры (без ИИ), отрисовка и игровой цикл.

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Tetris AI — эвристический агент")
clock = pygame.time.Clock()
font = pygame.font.Font(None, 44)
small_font = pygame.font.Font(None, 26)


def check_collision(grid, shape, offset_x, offset_y):
    for row in range(len(shape)):
        for col in range(len(shape[row])):
            if shape[row][col]:
                gx = offset_x + col
                gy = offset_y + row
                if gx < 0 or gx >= GRID_WIDTH or gy >= GRID_HEIGHT:
                    return True
                if gy < 0:
                    continue
                if grid[gy][gx] != 0:
                    return True
    return False


def rotate_matrix(shape):
    return [list(row) for row in zip(*shape[::-1])]


def get_all_rotations(shape):
    rotations = []
    current = shape
    for _ in range(4):
        if current not in rotations:
            rotations.append(current)
        current = rotate_matrix(current)
    return rotations


def drop_y(grid, shape, x):
    # глубина, на которую упадёт фигура в колонке x
    y = 0
    while not check_collision(grid, shape, x, y + 1):
        y += 1
    if check_collision(grid, shape, x, y):
        return None
    return y


def place_on_grid(grid, shape, x, y, value=1):
    new_grid = [row[:] for row in grid]
    for row in range(len(shape)):
        for col in range(len(shape[row])):
            if shape[row][col]:
                new_grid[y + row][x + col] = value
    return new_grid


def clear_full_lines(grid):
    remaining = [row for row in grid if any(c == 0 for c in row)]
    lines_cleared = GRID_HEIGHT - len(remaining)
    while len(remaining) < GRID_HEIGHT:
        remaining.insert(0, [0] * GRID_WIDTH)
    return remaining, lines_cleared


def get_random_shape():
    name = random.choice(list(SHAPES.keys()))
    shape, color = SHAPES[name]
    return name, [row[:] for row in shape], color


def spawn_piece():
    name, shape, color = get_random_shape()
    x = GRID_WIDTH // 2 - len(shape[0]) // 2
    y = 0
    return name, shape, color, x, y


def draw_block(x, y, color):
    rect = pygame.Rect(
        grid_left + x * GRID_SIZE, grid_top + y * GRID_SIZE, GRID_SIZE, GRID_SIZE
    )
    pygame.draw.rect(screen, color, rect)
    pygame.draw.rect(screen, GRAY, rect, 1)


def draw_shape(shape, offset_x, offset_y, color):
    for row in range(len(shape)):
        for col in range(len(shape[row])):
            if shape[row][col]:
                draw_block(offset_x + col, offset_y + row, color)


def draw_next_piece_box(shape, color, box_x, box_y):
    box_w = PREVIEW_COLS * PREVIEW_CELL
    box_h = PREVIEW_ROWS * PREVIEW_CELL
    pygame.draw.rect(
        screen,
        DARK_GRAY,
        (box_x - 5, box_y - 5, box_w + 10, box_h + 10),
        border_radius=4,
    )

    shape_w = len(shape[0]) * PREVIEW_CELL
    shape_h = len(shape) * PREVIEW_CELL
    offset_x = box_x + (box_w - shape_w) // 2
    offset_y = box_y + (box_h - shape_h) // 2

    for row in range(len(shape)):
        for col in range(len(shape[row])):
            if shape[row][col]:
                rect = pygame.Rect(
                    offset_x + col * PREVIEW_CELL,
                    offset_y + row * PREVIEW_CELL,
                    PREVIEW_CELL,
                    PREVIEW_CELL,
                )
                pygame.draw.rect(screen, color, rect)
                pygame.draw.rect(screen, GRAY, rect, 1)


#  состояние игры 
grid = [[0] * GRID_WIDTH for _ in range(GRID_HEIGHT)]
score = 0
lines_total = 0

current_name, current_shape, current_color, current_x, current_y = spawn_piece()
next_name, next_shape, next_color = get_random_shape()

plan = best_placement_two_step(grid, current_shape, next_shape)
plan_rotations_left, target_x = plan if plan else (0, current_x)

move_counter = 0
game_over = False

#  игровой цикл 
running = True
while running:
    clock.tick(60)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                running = False
            if event.key == pygame.K_r and game_over:
                grid = [[0] * GRID_WIDTH for _ in range(GRID_HEIGHT)]
                score = 0
                lines_total = 0
                game_over = False
                current_name, current_shape, current_color, current_x, current_y = (
                    spawn_piece()
                )
                next_name, next_shape, next_color = get_random_shape()
                plan = best_placement_two_step(grid, current_shape, next_shape)
                plan_rotations_left, target_x = plan if plan else (0, current_x)

    if not game_over:
        move_counter += 1
        if move_counter >= MOVE_DELAY:
            move_counter = 0

            if plan_rotations_left > 0:
                rotated = rotate_matrix(current_shape)
                if not check_collision(grid, rotated, current_x, current_y):
                    current_shape = rotated
                plan_rotations_left -= 1

            elif current_x < target_x:
                if not check_collision(grid, current_shape, current_x + 1, current_y):
                    current_x += 1
                else:
                    target_x = current_x

            elif current_x > target_x:
                if not check_collision(grid, current_shape, current_x - 1, current_y):
                    current_x -= 1
                else:
                    target_x = current_x

            else:
                if not check_collision(grid, current_shape, current_x, current_y + 1):
                    current_y += 1
                else:
                    grid = place_on_grid(
                        grid, current_shape, current_x, current_y, value=current_color
                    )
                    grid, cleared = clear_full_lines(grid)
                    if cleared:
                        score += LINE_SCORES.get(cleared, cleared * 400)
                        lines_total += cleared

                    current_name, current_shape, current_color = (
                        next_name,
                        next_shape,
                        next_color,
                    )
                    current_x = GRID_WIDTH // 2 - len(current_shape[0]) // 2
                    current_y = 0
                    next_name, next_shape, next_color = get_random_shape()

                    if check_collision(grid, current_shape, current_x, current_y):
                        game_over = True
                    else:
                        plan = best_placement_two_step(grid, current_shape, next_shape)
                        plan_rotations_left, target_x = plan if plan else (0, current_x)

    #  рендер 
    screen.fill(BLACK)

    pygame.draw.rect(
        screen,
        DARK_GRAY,
        (grid_left - 5, grid_top - 5, grid_width_px + 10, grid_height_px + 10),
        border_radius=4,
    )

    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            rect = pygame.Rect(
                grid_left + col * GRID_SIZE,
                grid_top + row * GRID_SIZE,
                GRID_SIZE,
                GRID_SIZE,
            )
            pygame.draw.rect(screen, GRAY, rect, 1)

    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            if grid[row][col]:
                draw_block(col, row, grid[row][col])

    if not game_over:
        draw_shape(current_shape, current_x, current_y, current_color)

    info_x = grid_left + grid_width_px + 50

    label = small_font.render("СЧЁТ", True, WHITE)
    screen.blit(label, (info_x, grid_top + 20))
    score_text = font.render(str(score), True, WHITE)
    screen.blit(score_text, (info_x, grid_top + 50))

    lines_label = small_font.render("ЛИНИИ", True, WHITE)
    screen.blit(lines_label, (info_x, grid_top + 110))
    lines_text = font.render(str(lines_total), True, WHITE)
    screen.blit(lines_text, (info_x, grid_top + 140))

    next_label = small_font.render("СЛЕДУЮЩАЯ", True, WHITE)
    screen.blit(next_label, (info_x, grid_top + 195))
    draw_next_piece_box(next_shape, next_color, info_x, grid_top + 225)

    if game_over:
        over_text = font.render("GAME OVER", True, RED)
        screen.blit(over_text, (info_x - 10, grid_top + 340))
        hint_text = small_font.render("R — заново, Esc — выход", True, WHITE)
        screen.blit(hint_text, (info_x - 10, grid_top + 380))

    pygame.display.flip()

pygame.quit()
sys.exit()
