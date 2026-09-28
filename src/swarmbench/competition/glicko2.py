"""V2's direct Glicko-2 implementation, retained under the MIT license."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log, pi, sqrt

GLICKO_SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_DEVIATION = 350.0
DEFAULT_VOLATILITY = 0.06
DEFAULT_TAU = 0.5


@dataclass(frozen=True, slots=True)
class GlickoRating:
    rating: float = DEFAULT_RATING
    deviation: float = DEFAULT_DEVIATION
    volatility: float = DEFAULT_VOLATILITY


def _g(phi: float) -> float:
    return 1.0 / sqrt(1.0 + 3.0 * phi * phi / (pi * pi))


def _expectation(mu: float, opponent_mu: float, opponent_phi: float) -> float:
    return 1.0 / (1.0 + exp(-_g(opponent_phi) * (mu - opponent_mu)))


def _new_volatility(phi: float, sigma: float, variance: float, delta: float, tau: float) -> float:
    a = log(sigma * sigma)

    def function(x: float) -> float:
        exponent = exp(x)
        numerator = exponent * (delta * delta - phi * phi - variance - exponent)
        return numerator / (2.0 * (phi * phi + variance + exponent) ** 2) - (x - a) / (tau * tau)

    point_a = a
    if delta * delta > phi * phi + variance:
        point_b = log(delta * delta - phi * phi - variance)
    else:
        k = 1
        while function(a - k * tau) < 0:
            k += 1
        point_b = a - k * tau
    value_a, value_b = function(point_a), function(point_b)
    while abs(point_b - point_a) > 1e-6:
        point_c = point_a + (point_a - point_b) * value_a / (value_b - value_a)
        value_c = function(point_c)
        if value_c * value_b <= 0:
            point_a, value_a = point_b, value_b
        else:
            value_a /= 2.0
        point_b, value_b = point_c, value_c
    return exp(point_a / 2.0)


def update_rating(player: GlickoRating, results: list[tuple[GlickoRating, float]], *, tau: float = DEFAULT_TAU) -> GlickoRating:
    mu = (player.rating - 1500.0) / GLICKO_SCALE
    phi = player.deviation / GLICKO_SCALE
    if not results:
        return GlickoRating(player.rating, min(350.0, sqrt(phi * phi + player.volatility**2) * GLICKO_SCALE), player.volatility)
    converted = [((opponent.rating - 1500.0) / GLICKO_SCALE, opponent.deviation / GLICKO_SCALE, float(score)) for opponent, score in results]
    inverse_variance = sum(_g(opponent_phi) ** 2 * expectation * (1.0 - expectation) for opponent_mu, opponent_phi, _ in converted for expectation in [_expectation(mu, opponent_mu, opponent_phi)])
    variance = 1.0 / inverse_variance
    improvement = sum(_g(opponent_phi) * (score - _expectation(mu, opponent_mu, opponent_phi)) for opponent_mu, opponent_phi, score in converted)
    delta = variance * improvement
    sigma = _new_volatility(phi, player.volatility, variance, delta, tau)
    phi_star = sqrt(phi * phi + sigma * sigma)
    new_phi = 1.0 / sqrt(1.0 / (phi_star * phi_star) + 1.0 / variance)
    new_mu = mu + new_phi * new_phi * improvement
    return GlickoRating(1500.0 + GLICKO_SCALE * new_mu, GLICKO_SCALE * new_phi, sigma)


def simultaneous_update(ratings: dict[str, GlickoRating], games: list[tuple[str, str, float]], *, tau: float = DEFAULT_TAU) -> dict[str, GlickoRating]:
    observations: dict[str, list[tuple[GlickoRating, float]]] = {key: [] for key in ratings}
    for left, right, score in games:
        if left not in ratings or right not in ratings or not 0.0 <= score <= 1.0:
            raise ValueError("invalid Glicko game")
        observations[left].append((ratings[right], score))
        observations[right].append((ratings[left], 1.0 - score))
    return {key: update_rating(rating, observations[key], tau=tau) for key, rating in ratings.items()}
