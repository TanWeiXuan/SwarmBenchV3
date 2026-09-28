"""Lantern Phalanx — an original decentralized controller by OpenAI Codex (GPT-6).

Eight independent lanterns share sightings, not privileged state. Each worker
keeps its own observed map, plans bounded A* routes, and fights at standoff range.
Radio carries the sender position and one timestamped, velocity-bearing contact.
Forward spotters maintain vision while supporting units exploit weapon reach.
Very fresh radio shots are speculative through unknown terrain, but never through
a known wall; local and radio friendly positions gate every firing request.
Reports retain their original timestamp when relayed; absence is never a kill.

Strategy/code attribution: written for this submission. The documented built-in
baselines were inspected for API usage and comparison; no baseline code is used.
Only public swarmbench API types and Python's standard library are imported.
"""

import heapq
import math

from swarmbench import Action, BaseUnitController


def norm(x, y, maximum=4.0):
    length = math.hypot(x, y)
    if length > maximum:
        return x * maximum / length, y * maximum / length
    return x, y


def delta(a, b):
    return (b - a + math.pi) % (2 * math.pi) - math.pi


def ray_disc(p, direction, q, radius):
    x, y = q[0] - p[0], q[1] - p[1]
    along = x * direction[0] + y * direction[1]
    across = x * direction[1] - y * direction[0]
    if abs(across) > radius:
        return math.inf
    half = math.sqrt(max(0.0, radius * radius - across * across))
    return max(0.0, along - half) if along + half >= 0 else math.inf


class UnitController(BaseUnitController):
    def initialize(self, info):
        self.rules = info.rules
        self.uid = info.unit_id
        self.width, self.height = info.arena_size
        self.forward = 1 if info.starting_state.position[0] < self.width / 2 else -1
        self.known = {}  # Only TerrainCell observations populate this map.
        self.friends = {}
        self.spotters = {}
        self.contacts = {}  # id -> (x, y, vx, vy, original_tick)
        self.path = []
        self.path_goal = None
        self.plan_tick = -100
        self.last_position = info.starting_state.position
        self.stuck = 0
        self.target_id = None
        self.last_contact = -100
        self.patrol = 0

    def wall(self, x, y):
        return x < 0 or y < 0 or x >= self.width or y >= self.height or self.known.get((x, y), False)

    def fits(self, x, y, radius=0.43):
        for cy in range(math.floor(y - radius), math.floor(y + radius) + 1):
            for cx in range(math.floor(x - radius), math.floor(x + radius) + 1):
                if self.wall(cx, cy):
                    dx = max(cx - x, 0.0, x - cx - 1)
                    dy = max(cy - y, 0.0, y - cy - 1)
                    if dx * dx + dy * dy < radius * radius:
                        return False
        return True

    def segment(self, p, q, radius=0.43):
        length = math.dist(p, q)
        count = max(1, math.ceil(length / 0.24))
        for i in range(1, count + 1):
            t = i / count
            if not self.fits(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, radius):
                return False
        return True

    def sight(self, p, q, require_known=False):
        """Conservative grid traversal, including both sides of corner crossings."""
        x, y = math.floor(p[0]), math.floor(p[1])
        ex, ey = math.floor(q[0]), math.floor(q[1])
        dx, dy = q[0] - p[0], q[1] - p[1]
        sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        tx = ((x + 1 - p[0]) if dx > 0 else (p[0] - x)) / abs(dx) if dx else math.inf
        ty = ((y + 1 - p[1]) if dy > 0 else (p[1] - y)) / abs(dy) if dy else math.inf
        ix, iy = (1 / abs(dx) if dx else math.inf), (1 / abs(dy) if dy else math.inf)
        for _ in range(260):
            if self.wall(x, y) or (require_known and (x, y) not in self.known):
                return False
            if (x, y) == (ex, ey):
                return True
            if abs(tx - ty) < 1e-9:
                if self.wall(x + sx, y) or self.wall(x, y + sy):
                    return False
                if require_known and ((x + sx, y) not in self.known or (x, y + sy) not in self.known):
                    return False
                x, y, tx, ty = x + sx, y + sy, tx + ix, ty + iy
            elif tx < ty:
                x, tx = x + sx, tx + ix
            else:
                y, ty = y + sy, ty + iy
        return False

    def receive(self, obs):
        tick = obs.tick
        self.spotters = {}
        for cell in obs.visible_terrain:
            self.known[(cell.x, cell.y)] = cell.blocked
        for msg in obs.radio:
            packet = msg.payload
            x, y = (packet & 1023) / 8, ((packet >> 10) & 1023) / 8
            if not (0 <= x < self.width and 0 <= y < self.height):
                continue
            old = self.friends.get(msg.sender_id)
            vx, vy = 0.0, 0.0
            velocity_error = 1.8
            if old and tick - 1 == old[4]:
                # A same-tick exact sighting is better than a quantized packet.
                x, y, vx, vy = old[:4]
                velocity_error = old[5]
            elif old and tick - 1 > old[4]:
                dt = (tick - 1 - old[4]) * 0.1
                vx, vy = norm((x - old[0]) / dt, (y - old[1]) / dt)
                velocity_error = min(8.0, 0.18 / dt + 4 * dt)
            else:
                velocity_error = self.rules.max_speed
            self.friends[msg.sender_id] = (x, y, vx, vy, tick - 1, velocity_error)
            if (packet >> 62) & 1:
                eid = (packet >> 40) & 7
                stamp = (packet >> 43) & 2047
                ex, ey = ((packet >> 20) & 1023) / 8, ((packet >> 30) & 1023) / 8
                evx, evy = (((packet >> 54) & 15) - 7) * 0.6, (((packet >> 58) & 15) - 7) * 0.6
                previous = self.contacts.get(eid)
                if stamp == tick - 1:
                    self.spotters[eid] = min(msg.sender_id, self.spotters.get(eid, 8))
                if 0 <= tick - stamp <= 25 and 0 <= ex < self.width and 0 <= ey < self.height:
                    if previous is None or stamp > previous[4]:
                        self.contacts[eid] = (ex, ey, evx, evy, stamp)
        for unit in obs.visible_friendlies:
            self.friends[unit.unit_id] = (*unit.position, *unit.velocity, tick, 0.0)
        for unit in obs.visible_enemies:
            self.contacts[unit.unit_id] = (*unit.position, *unit.velocity, tick)
            self.last_contact = tick
        self.friends = {i: f for i, f in self.friends.items() if tick - f[4] <= 5}
        self.contacts = {i: c for i, c in self.contacts.items() if tick - c[4] <= 55}

    def packet(self, obs, contact_id):
        # Bits: self x/y 10+10, enemy x/y 10+10 (eighth metres), enemy ID 3,
        # original tick 11, velocity x/y 4+4 (0.6 m/s), contact-present 1.
        # This is 63 bits; forwarding never changes the original observation tick.
        x, y = obs.self_state.position
        packet = min(1023, max(0, round(x * 8))) | (min(1023, max(0, round(y * 8))) << 10)
        c = self.contacts.get(contact_id)
        if c is not None and 0 <= obs.tick - c[4] <= 25:
            ex, ey, vx, vy, tick = c
            packet |= min(1023, max(0, round(ex * 8))) << 20
            packet |= min(1023, max(0, round(ey * 8))) << 30
            packet |= contact_id << 40
            packet |= tick << 43
            packet |= min(15, max(0, round(vx / 0.6) + 7)) << 54
            packet |= min(15, max(0, round(vy / 0.6) + 7)) << 58
            packet |= 1 << 62
        return packet

    def route(self, p, goal, tick):
        goal = (min(self.width - 2, max(1, int(goal[0]))), min(self.height - 2, max(1, int(goal[1]))))
        start = int(p[0]), int(p[1])
        invalid = any(self.wall(*cell) for cell in self.path[:12])
        changed = self.path_goal is None or math.dist(goal, self.path_goal) > 4
        if not self.path or invalid or changed or tick - self.plan_tick >= 25 or self.stuck == 10:
            self.plan_tick, self.path_goal = tick, goal
            # A bounded weighted A*: unknown space is optimistic, never a map oracle.
            def heuristic(cell):
                dx, dy = abs(goal[0] - cell[0]), abs(goal[1] - cell[1])
                return max(dx, dy) + 0.414214 * min(dx, dy)
            queue = [(1.15 * heuristic(start), 0.0, start)]
            costs, parent = {start: 0.0}, {}
            best, best_h = start, heuristic(start)
            for _ in range(2200):
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
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    other = x + dx, y + dy
                    if self.wall(*other) or (dx and dy and (self.wall(x + dx, y) or self.wall(x, y + dy))):
                        continue
                    extra = 1.414214 if dx and dy else 1.0
                    if other not in self.known:
                        extra *= 1.12
                    new = cost + extra
                    if new < costs.get(other, math.inf):
                        costs[other], parent[other] = new, cell
                        heapq.heappush(queue, (new + 1.15 * heuristic(other), new, other))
            path = []
            while best != start and best in parent:
                path.append(best)
                best = parent[best]
            self.path = list(reversed(path))
        while self.path and math.dist(p, (self.path[0][0] + 0.5, self.path[0][1] + 0.5)) < 0.65:
            self.path.pop(0)
        waypoint = p
        for cell in self.path[:7]:
            candidate = cell[0] + 0.5, cell[1] + 0.5
            if self.segment(p, candidate):
                waypoint = candidate
            else:
                break
        if waypoint == p and self.path:
            waypoint = self.path[0][0] + 0.5, self.path[0][1] + 0.5
        return norm((waypoint[0] - p[0]) * 3, (waypoint[1] - p[1]) * 3)

    def local_velocity(self, obs, preferred, threat):
        p, current = obs.self_state.position, obs.self_state.velocity
        candidates = [preferred, (0.0, 0.0), current]
        angle = math.atan2(preferred[1], preferred[0])
        for turn in (0, 0.4, -0.4, 0.85, -0.85, 1.4, -1.4, 2.1, -2.1, math.pi):
            a = angle + turn
            candidates.append((4 * math.cos(a), 4 * math.sin(a)))
        friends = []
        for fid, f in self.friends.items():
            age = (obs.tick - f[4]) * 0.1
            q = (f[0] + f[2] * age, f[1] + f[3] * age)
            if math.dist(p, q) < 5:
                friends.append((fid, q, (f[2], f[3])))
        best, best_score = (0.0, 0.0), -math.inf
        for wanted in candidates:
            wanted = norm(*wanted)
            vx, vy = current
            x, y = p
            score = -0.09 * math.dist(wanted, preferred) ** 2
            valid = True
            for step in range(1, 7):
                ax, ay = norm(wanted[0] - vx, wanted[1] - vy, 0.4)
                vx, vy = vx + ax, vy + ay
                x, y = x + vx * 0.05, y + vy * 0.05
                if not self.fits(x, y):
                    valid = False
                    break
                t = step * 0.05
                for fid, q, fv in friends:
                    separation = math.hypot(x - q[0] - fv[0] * t, y - q[1] - fv[1] * t)
                    if separation < 0.86:
                        score -= 30 + (0.86 - separation) * 100
                    elif separation < 1.7:
                        score -= (1.7 - separation) * 1.2
            if not valid:
                continue
            score += ((x - p[0]) * preferred[0] + (y - p[1]) * preferred[1]) * 0.7
            if threat is not None:
                # Prefer lateral motion when a visible opponent is aiming at us.
                q, heading = threat
                d = math.dist(p, q)
                if d > 1:
                    aim = abs(delta(heading, math.atan2(p[1] - q[1], p[0] - q[0])))
                    if aim < 0.18:
                        lateral = abs((x - p[0]) * (p[1] - q[1]) - (y - p[1]) * (p[0] - q[0])) / d
                        score += lateral * 2.5
            if score > best_score:
                best, best_score = wanted, score
        return best

    def friendly_clear(self, obs, direction, distance):
        p = obs.self_state.position
        for f in self.friends.values():
            age = (obs.tick - f[4]) * 0.1
            dx, dy = f[2] * age, f[3] * age
            q = f[0] + dx * 0.5, f[1] + dy * 0.5
            # Cover the whole last-position/prediction interval: contacts can
            # stop or slide a teammate abruptly, without an acceleration ramp.
            uncertainty = (0.09 if f[5] else 0.0) + f[5] * age + 4 * age * age
            uncertainty += math.hypot(dx, dy) * 0.5
            if ray_disc(p, direction, q, 0.42 + uncertainty) <= distance:
                return False
        return True

    def step(self, obs):
        self.receive(obs)
        state, tick = obs.self_state, obs.tick
        p = state.position
        self.stuck = self.stuck + 1 if math.dist(p, self.last_position) < 0.035 else 0
        self.last_position = p
        visible = {unit.unit_id: unit for unit in obs.visible_enemies}
        options = []
        for eid, c in self.contacts.items():
            age = tick - c[4]
            predicted = (c[0] + c[2] * min(age, 3) * 0.1, c[1] + c[3] * min(age, 3) * 0.1)
            distance = math.dist(p, predicted)
            if age > 25:
                continue
            score = distance * 0.45 + age * 0.3 + eid * 0.28
            if eid == self.target_id:
                score -= 1.8
            if eid in visible:
                score -= 4
            a = math.atan2(predicted[1] - p[1], predicted[0] - p[0])
            if not self.friendly_clear(obs, (math.cos(a), math.sin(a)), distance):
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
            if fresh and distance < 22 and self.sight(p, target):
                dx, dy = (target[0] - p[0]) / max(0.1, distance), (target[1] - p[1]) / max(0.1, distance)
                # Fresh immediate reports identify active observers. A lower-ID
                # local observer can fill in if the nominal spotters are absent.
                scout = self.uid % 3 == 0 or self.uid <= self.spotters.get(self.target_id, 8)
                ideal = (17.0 if scout else 19.5) if not state.reloading else 20.5
                if state.health <= 40:
                    ideal += 1.5
                radial = max(-4.0, min(3.0, (distance - ideal) * 1.6))
                # Stable lines aid accurate, shared fire; only sidestep blockers.
                obstructed = not self.friendly_clear(obs, (dx, dy), distance)
                sidestep = (2.6 if obstructed else 0.0) * (1 if ((tick // 31 + self.uid) % 2) else -1)
                preferred = norm(dx * radial - dy * sidestep, dy * radial + dx * sidestep)
            else:
                preferred = self.route(p, target, tick)
        else:
            # A broad central formation concentrates fire without single-file traffic.
            lane = self.height / 2 + (self.uid - 3.5) * 2.3
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
            preferred = self.route(p, goal, tick)
        if not chosen:
            # Let nearby support catch up before an isolated unit finds combat.
            progress = sorted(self.forward * f[0] for f in self.friends.values())
            if len(progress) >= 3:
                lead = self.forward * p[0] - progress[len(progress) // 2]
                if lead > 3 and self.forward * preferred[0] > 0:
                    preferred = norm(*preferred, max(0.5, 4 - (lead - 3) * 0.7))
        velocity = self.local_velocity(obs, preferred, threat)
        if chosen:
            c = self.contacts[self.target_id]
            # Lead the NEXT control boundary, never the instantaneous hitscan shot.
            ax, ay = norm(velocity[0] - state.velocity[0], velocity[1] - state.velocity[1], 0.8)
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
        direction = math.cos(state.heading), math.sin(state.heading)
        if state.ammunition and not state.reloading and state.cooldown_remaining <= 1e-8:
            hits = []
            for eid, c in self.contacts.items():
                age = tick - c[4]
                if age > 2:
                    continue
                q = c[0] + c[2] * age * 0.1, c[1] + c[3] * age * 0.1
                hit = ray_disc(p, direction, q, 0.395 if eid in visible else 0.24)
                if hit <= self.rules.weapon_range:
                    end = p[0] + direction[0] * hit, p[1] + direction[1] * hit
                    if self.sight(p, end, require_known=False):
                        hits.append((hit, eid in visible))
            if hits:
                hit, confirmed = min(hits)
                # A speculative target may have moved or died. Protect teammates
                # BEHIND it as well, because such a shot can travel the full range.
                safe_distance = hit if confirmed else self.rules.weapon_range
                fire = self.friendly_clear(obs, direction, safe_distance)
        reload_request = state.ammunition == 0 or (state.ammunition <= 3 and not visible and (not chosen or chosen[3] > 22) and tick - self.last_contact > 12)
        report_id = self.target_id
        if visible:
            # Prefer the combat focus; otherwise report the freshest local contact.
            if report_id not in visible:
                report_id = min(visible)
        elif options:
            report_id = min(self.contacts, key=lambda i: (-self.contacts[i][4], i))
        return Action(velocity, heading, bool(fire), bool(reload_request), self.packet(obs, report_id))
