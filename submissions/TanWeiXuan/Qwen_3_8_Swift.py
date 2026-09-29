"""Qwen 3.8 Swift — a fast, decentralized SwarmBenchV3 controller.

Eight independent units coordinate through a compact 64-bit radio beacon.
Each worker keeps only its own observed wall map, routes with a bounded
weighted A*, relays fresh enemy contacts, and fights at a safe standoff
range. Every firing request is gated by a friendly-clearance check so the
team does not burn shots (or lives) on its own units, and units reload only
when they are genuinely out of the fight.

The design optimizes for unseen opponents and hidden seeds: wide lane spacing
limits friendly fire, a single shared focus target concentrates fire, and all
navigation degrades gracefully to local wall avoidance when the map is unknown.

Strategy/code attribution: original work for this submission, generated with
the Qwen 3.8 Swift model. Only the public swarmbench API and the Python
standard library are imported; no baseline code is reused.
"""

import heapq
import math

from swarmbench import Action, BaseUnitController

MAX_SPEED = 4.0
CLEARANCE = 0.43
STANDOFF = 18.0
REPLAN_TICKS = 20
A_STAR_BUDGET = 1500
CONTACT_AGE = 40
FRIEND_AGE = 6


def clamp_vec(x, y, maximum=MAX_SPEED):
    length = math.hypot(x, y)
    if length > maximum:
        return x * maximum / length, y * maximum / length
    return x, y


def angle_delta(a, b):
    return (b - a + math.pi) % (2 * math.pi) - math.pi


def ray_disc(p, direction, q, radius):
    """Distance along `direction` from p to the near side of disc(q, radius)."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    along = rx * direction[0] + ry * direction[1]
    across = rx * direction[1] - ry * direction[0]
    if abs(across) > radius:
        return math.inf
    half = math.sqrt(max(0.0, radius * radius - across * across))
    if along + half < 0:
        return math.inf
    return max(0.0, along - half)


class UnitController(BaseUnitController):
    def initialize(self, info):
        self.rules = info.rules
        self.uid = info.unit_id
        self.width, self.height = info.arena_size
        self.forward = 1 if info.starting_state.position[0] < self.width / 2 else -1
        self.known = {}
        self.friends = {}
        self.contacts = {}
        self.path = []
        self.path_goal = None
        self.plan_tick = -100
        self.last_position = info.starting_state.position
        self.stuck = 0
        self.target_id = None
        self.last_contact = -100
        self.patrol = 0

    # ---- terrain ----
    def _wall(self, x, y):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return True
        return self.known.get((x, y), False)

    def _fits(self, x, y, radius=CLEARANCE):
        for cy in range(math.floor(y - radius), math.floor(y + radius) + 1):
            for cx in range(math.floor(x - radius), math.floor(x + radius) + 1):
                if self._wall(cx, cy):
                    dx = max(cx - x, 0.0, x - cx - 1)
                    dy = max(cy - y, 0.0, y - cy - 1)
                    if dx * dx + dy * dy < radius * radius:
                        return False
        return True

    def _segment_clear(self, p, q, radius=CLEARANCE):
        length = math.dist(p, q)
        count = max(1, math.ceil(length / 0.25))
        for i in range(1, count + 1):
            t = i / count
            if not self._fits(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, radius):
                return False
        return True

    def _line_of_sight(self, p, q):
        """Conservative supercover traversal; both sides of a corner count."""
        x, y = math.floor(p[0]), math.floor(p[1])
        ex, ey = math.floor(q[0]), math.floor(q[1])
        dx, dy = q[0] - p[0], q[1] - p[1]
        sx = 1 if dx > 0 else -1
        sy = 1 if dy > 0 else -1
        tx = ((x + 1 - p[0]) if dx > 0 else (p[0] - x)) / abs(dx) if dx else math.inf
        ty = ((y + 1 - p[1]) if dy > 0 else (p[1] - y)) / abs(dy) if dy else math.inf
        ix = 1 / abs(dx) if dx else math.inf
        iy = 1 / abs(dy) if dy else math.inf
        for _ in range(260):
            if self._wall(x, y):
                return False
            if (x, y) == (ex, ey):
                return True
            if abs(tx - ty) < 1e-9:
                if self._wall(x + sx, y) or self._wall(x, y + sy):
                    return False
                x, y, tx, ty = x + sx, y + sy, tx + ix, ty + iy
            elif tx < ty:
                x, tx = x + sx, tx + ix
            else:
                y, ty = y + sy, ty + iy
        return False

    # ---- sensing and radio ----
    def _receive(self, obs):
        tick = obs.tick
        for cell in obs.visible_terrain:
            self.known[(cell.x, cell.y)] = cell.blocked
        for msg in obs.radio:
            packet = msg.payload
            if not (0 <= packet <= 2**64 - 1):
                continue
            x = (packet & 1023) / 8
            y = ((packet >> 10) & 1023) / 8
            if not (0 <= x < self.width and 0 <= y < self.height):
                continue
            old = self.friends.get(msg.sender_id)
            vx, vy = 0.0, 0.0
            if old is not None and tick - 1 == old[4]:
                x, y, vx, vy = old[0], old[1], old[2], old[3]
            elif old is not None and tick - 1 > old[4]:
                dt = (tick - 1 - old[4]) * 0.1
                if dt > 0:
                    vx, vy = clamp_vec((x - old[0]) / dt, (y - old[1]) / dt)
            self.friends[msg.sender_id] = (x, y, vx, vy, tick - 1)
            if (packet >> 20) & 1:
                eid = (packet >> 21) & 7
                stamp = (packet >> 24) & 2047
                ex = ((packet >> 34) & 1023) / 8
                ey = ((packet >> 44) & 1023) / 8
                evx = (((packet >> 55) & 15) - 8) * 0.5
                evy = (((packet >> 59) & 15) - 8) * 0.5
                if 0 <= ex < self.width and 0 <= ey < self.height and 0 <= tick - stamp <= 30:
                    previous = self.contacts.get(eid)
                    if previous is None or stamp > previous[4]:
                        self.contacts[eid] = (ex, ey, evx, evy, stamp)
        for unit in obs.visible_friendlies:
            self.friends[unit.unit_id] = (*unit.position, *unit.velocity, tick)
        for unit in obs.visible_enemies:
            self.contacts[unit.unit_id] = (*unit.position, *unit.velocity, tick)
            self.last_contact = tick
        self.friends = {i: f for i, f in self.friends.items() if tick - f[4] <= FRIEND_AGE}
        self.contacts = {i: c for i, c in self.contacts.items() if tick - c[4] <= CONTACT_AGE}

    def _packet(self, obs, contact_id):
        # Bits: self x/y 10+10, contact-present 1, enemy id 3, enemy x/y 10+10,
        # original tick 11, enemy vx/vy 4+4. Relaying keeps the original stamp.
        x, y = obs.self_state.position
        packet = min(1023, max(0, round(x * 8))) | (min(1023, max(0, round(y * 8))) << 10)
        c = self.contacts.get(contact_id)
        if c is not None and 0 <= obs.tick - c[4] <= 30:
            ex, ey, vx, vy, stamp = c
            packet |= 1 << 20
            packet |= (contact_id & 7) << 21
            packet |= min(1023, max(0, round(ex * 8))) << 24
            packet |= min(1023, max(0, round(ey * 8))) << 34
            packet |= (stamp & 2047) << 44
            packet |= min(15, max(0, round(vx / 0.5) + 8)) << 55
            packet |= min(15, max(0, round(vy / 0.5) + 8)) << 59
        return packet

    # ---- navigation ----
    def _route(self, p, goal, tick):
        goal = (min(self.width - 2, max(1, int(goal[0]))),
                min(self.height - 2, max(1, int(goal[1]))))
        start = (int(p[0]), int(p[1]))
        invalid = any(self._wall(*cell) for cell in self.path[:12])
        changed = self.path_goal is None or math.dist(goal, self.path_goal) > 4
        if (not self.path) or invalid or changed or tick - self.plan_tick >= REPLAN_TICKS or self.stuck == 8:
            self.plan_tick, self.path_goal = tick, goal

            def heuristic(cell):
                dx, dy = abs(goal[0] - cell[0]), abs(goal[1] - cell[1])
                return max(dx, dy) + 0.414214 * min(dx, dy)

            queue = [(1.1 * heuristic(start), 0.0, start)]
            costs, parent = {start: 0.0}, {}
            best, best_h = start, heuristic(start)
            for _ in range(A_STAR_BUDGET):
                if not queue:
                    break
                _, cost, cell = heapq.heappop(queue)
                if cost > costs.get(cell, math.inf):
                    continue
                h = heuristic(cell)
                if h < best_h:
                    best, best_h = cell, h
                if cell == goal:
                    best = cell
                    break
                x, y = cell
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                                (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    other = (x + dx, y + dy)
                    if self._wall(*other):
                        continue
                    if dx and dy and (self._wall(x + dx, y) or self._wall(x, y + dy)):
                        continue
                    extra = 1.414214 if (dx and dy) else 1.0
                    if other not in self.known:
                        extra *= 1.1
                    new = cost + extra
                    if new < costs.get(other, math.inf):
                        costs[other] = new
                        parent[other] = cell
                        heapq.heappush(queue, (new + 1.1 * heuristic(other), new, other))
            path = []
            while best != start and best in parent:
                path.append(best)
                best = parent[best]
            self.path = list(reversed(path))
        while self.path and math.dist(p, (self.path[0][0] + 0.5, self.path[0][1] + 0.5)) < 0.6:
            self.path.pop(0)
        waypoint = p
        for cell in self.path[:6]:
            candidate = (cell[0] + 0.5, cell[1] + 0.5)
            if self._segment_clear(p, candidate):
                waypoint = candidate
            else:
                break
        if waypoint == p and self.path:
            waypoint = (self.path[0][0] + 0.5, self.path[0][1] + 0.5)
        return clamp_vec((waypoint[0] - p[0]) * 3, (waypoint[1] - p[1]) * 3)

    def _choose_velocity(self, obs, preferred, threat):
        p, current = obs.self_state.position, obs.self_state.velocity
        candidates = [preferred, (0.0, 0.0), current]
        angle = math.atan2(preferred[1], preferred[0])
        for turn in (0, 0.4, -0.4, 0.85, -0.85, 1.4, -1.4, 2.1, -2.1, math.pi):
            a = angle + turn
            candidates.append((MAX_SPEED * math.cos(a), MAX_SPEED * math.sin(a)))
        friends = []
        for fid, f in self.friends.items():
            age = (obs.tick - f[4]) * 0.1
            q = (f[0] + f[2] * age, f[1] + f[3] * age)
            if math.dist(p, q) < 5:
                friends.append((fid, q, (f[2], f[3])))
        best, best_score = (0.0, 0.0), -math.inf
        for wanted in candidates:
            wanted = clamp_vec(*wanted)
            vx, vy = current
            x, y = p
            score = -0.09 * math.dist(wanted, preferred) ** 2
            valid = True
            for step in range(1, 7):
                ax, ay = clamp_vec(wanted[0] - vx, wanted[1] - vy, 0.4)
                vx, vy = vx + ax, vy + ay
                x, y = x + vx * 0.05, y + vy * 0.05
                if not self._fits(x, y):
                    valid = False
                    break
                t = step * 0.05
                for fid, q, fv in friends:
                    separation = math.hypot(x - q[0] - fv[0] * t, y - q[1] - fv[1] * t)
                    if separation < 0.85:
                        score -= 30 + (0.85 - separation) * 100
                    elif separation < 1.6:
                        score -= (1.6 - separation) * 1.2
            if not valid:
                continue
            score += ((x - p[0]) * preferred[0] + (y - p[1]) * preferred[1]) * 0.7
            if threat is not None:
                q, heading = threat
                d = math.dist(p, q)
                if d > 1:
                    aim = abs(angle_delta(heading, math.atan2(p[1] - q[1], p[0] - q[0])))
                    if aim < 0.18:
                        lateral = abs((x - p[0]) * (p[1] - q[1]) - (y - p[1]) * (p[0] - q[0])) / d
                        score += lateral * 2.5
            if score > best_score:
                best, best_score = wanted, score
        return best

    def _friendly_clear(self, obs, direction, distance):
        p = obs.self_state.position
        for f in self.friends.values():
            age = (obs.tick - f[4]) * 0.1
            q = (f[0] + f[2] * age * 0.5, f[1] + f[3] * age * 0.5)
            uncertainty = 0.09 + 4 * age * age
            uncertainty += math.hypot(f[2], f[3]) * age * 0.5
            if ray_disc(p, direction, q, 0.42 + uncertainty) <= distance:
                return False
        return True

    # ---- main loop ----
    def step(self, obs):
        self._receive(obs)
        state, tick = obs.self_state, obs.tick
        p = state.position
        self.stuck = self.stuck + 1 if math.dist(p, self.last_position) < 0.035 else 0
        self.last_position = p
        visible = {unit.unit_id: unit for unit in obs.visible_enemies}

        options = []
        for eid, c in self.contacts.items():
            age = tick - c[4]
            if age > 30:
                continue
            predicted = (c[0] + c[2] * min(age, 3) * 0.1,
                         c[1] + c[3] * min(age, 3) * 0.1)
            distance = math.dist(p, predicted)
            score = distance * 0.45 + age * 0.3 + eid * 0.28
            if eid == self.target_id:
                score -= 1.8
            if eid in visible:
                score -= 4
            a = math.atan2(predicted[1] - p[1], predicted[0] - p[0])
            if not self._friendly_clear(obs, (math.cos(a), math.sin(a)), distance):
                score += 4
            options.append((score, eid, predicted, distance))
        chosen = min(options) if options else None
        threat = None
        if chosen:
            _, self.target_id, target, distance = chosen
            contact = self.contacts[self.target_id]
            fresh = tick - contact[4] <= 3
            if self.target_id in visible:
                unit = visible[self.target_id]
                threat = (unit.position, unit.heading)
            if fresh and distance < self.rules.weapon_range and self._line_of_sight(p, target):
                dx, dy = (target[0] - p[0]) / max(0.1, distance), (target[1] - p[1]) / max(0.1, distance)
                ideal = STANDOFF if not state.reloading else STANDOFF + 2.5
                if state.health <= 40:
                    ideal += 1.5
                radial = max(-4.0, min(3.0, (distance - ideal) * 1.6))
                obstructed = not self._friendly_clear(obs, (dx, dy), distance)
                sidestep = (2.4 if obstructed else 0.0) * (1 if ((tick // 31 + self.uid) % 2) else -1)
                preferred = clamp_vec(dx * radial - dy * sidestep, dy * radial + dx * sidestep)
            else:
                preferred = self._route(p, target, tick)
        else:
            lane = self.height / 2 + (self.uid - 3.5) * 2.4
            far_x = self.width - 9 if self.forward > 0 else 9
            goal = (far_x, lane)
            if abs(p[0] - far_x) < 5 or self.patrol:
                if not self.patrol:
                    self.patrol = 1
                phase = (self.patrol - 1) % 4
                goal = ((self.width * (0.72 if self.forward > 0 else 0.28), 12 + self.uid * 2),
                        (self.width * 0.5, self.height - 12 - self.uid * 2),
                        ((9 if self.forward > 0 else self.width - 9), self.height - 12 - self.uid * 2),
                        (far_x, lane))[phase]
                if math.dist(p, goal) < 4:
                    self.patrol += 1
            preferred = self._route(p, goal, tick)

        velocity = self._choose_velocity(obs, preferred, threat)

        if chosen:
            c = self.contacts[self.target_id]
            ax, ay = clamp_vec(velocity[0] - state.velocity[0], velocity[1] - state.velocity[1], 0.8)
            future_self = (p[0] + (state.velocity[0] + ax * 0.75) * 0.1,
                           p[1] + (state.velocity[1] + ay * 0.75) * 0.1)
            future_target = (target[0] + c[2] * 0.1, target[1] + c[3] * 0.1)
            heading = math.atan2(future_target[1] - future_self[1], future_target[0] - future_self[0])
        else:
            heading = math.atan2(preferred[1], preferred[0])
            if self.stuck > 6 or math.hypot(*preferred) < 0.1:
                heading = state.heading + 0.5
            elif tick % 100 < 15:
                heading += (1 if self.uid % 2 else -1) * 0.75

        fire = False
        direction = (math.cos(state.heading), math.sin(state.heading))
        if state.ammunition and not state.reloading and state.cooldown_remaining <= 1e-8:
            hits = []
            for eid, c in self.contacts.items():
                age = tick - c[4]
                if age > 2:
                    continue
                q = (c[0] + c[2] * age * 0.1, c[1] + c[3] * age * 0.1)
                hit = ray_disc(p, direction, q, 0.395 if eid in visible else 0.24)
                if hit <= self.rules.weapon_range:
                    end = (p[0] + direction[0] * hit, p[1] + direction[1] * hit)
                    if self._line_of_sight(p, end):
                        hits.append((hit, eid in visible))
            if hits:
                hit, confirmed = min(hits)
                safe_distance = hit if confirmed else self.rules.weapon_range
                fire = self._friendly_clear(obs, direction, safe_distance)

        reload_request = state.ammunition == 0 or (
            state.ammunition <= 3 and not visible
            and (not chosen or chosen[3] > self.rules.weapon_range)
            and tick - self.last_contact > 12
        )

        report_id = self.target_id
        if visible:
            if report_id not in visible:
                report_id = min(visible)
        elif options:
            report_id = min(self.contacts, key=lambda i: (-self.contacts[i][4], i))

        return Action(velocity, heading, bool(fire), bool(reload_request), self._packet(obs, report_id))
