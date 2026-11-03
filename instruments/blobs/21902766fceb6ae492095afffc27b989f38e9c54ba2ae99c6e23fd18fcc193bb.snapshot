"""One atomic reservation/consumption ledger, owned by the provider clock."""
import math
import heapq
import threading
import time


class Ledger:
    def __init__(self, units=40000, requests=1000, clock=time.monotonic):
        self.limit = units
        self.request_limit = requests
        self.clock = clock
        self.lock = threading.RLock()
        self.windows = {}
        self.reservations = {}
        self.events = []
        self.expirations = []

    def _event(self, action, identifier, now, **values):
        self.events.append({'action': action, 'reservation': identifier, 'at': now, **values})

    def _expire(self, now):
        while self.expirations and self.expirations[0][0] <= now:
            _, key = heapq.heappop(self.expirations)
            reservation = self.reservations[key]
            if reservation['state'] == 'reserved':
                self._release(key, reservation, 'expired', now)

    def _release(self, key, reservation, state, now):
        window = self.windows[reservation['window']]
        window['committed_units'] -= reservation['units']
        window['committed_requests'] -= 1
        reservation['state'] = state
        self._event(state, key, now, window=reservation['window'], units=reservation['units'])

    def reserve(self, identifier, units, ttl=.25):
        if not isinstance(units, int) or units <= 0:
            raise ValueError('Positive integer quota units required')
        with self.lock:
            now = self.clock()
            self._expire(now)
            previous = self.reservations.get(identifier)
            if previous:
                return {'allowed': previous['state'] == 'reserved' and previous['units'] == units,
                        'reservation': identifier, **previous, 'duplicate': True}
            window_id = math.floor(now)
            window = self.windows.setdefault(window_id, {'committed_units': 0, 'committed_requests': 0,
                                                         'consumed_units': 0, 'consumed_requests': 0})
            allowed = (ttl > 0 and window['committed_units'] + units <= self.limit
                       and window['committed_requests'] < self.request_limit)
            if not allowed:
                self._event('denied', identifier, now, window=window_id, units=units)
                return {'allowed': False, 'retry_after': max(.001, window_id + 1 - now)}
            reservation = {'state': 'reserved', 'window': window_id, 'units': units,
                           'created': now, 'expires': min(now + ttl, window_id + 1)}
            self.reservations[identifier] = reservation
            heapq.heappush(self.expirations, (reservation['expires'], identifier))
            window['committed_units'] += units
            window['committed_requests'] += 1
            self._event('reserved', identifier, now, window=window_id, units=units)
            return {'allowed': True, 'reservation': identifier, **reservation}

    def consume(self, identifier, units):
        with self.lock:
            now = self.clock()
            self._expire(now)
            reservation = self.reservations.get(identifier)
            if not reservation or reservation['state'] != 'reserved' or reservation['units'] != units:
                self._event('consume_refused', identifier, now, units=units,
                            state=reservation['state'] if reservation else 'unknown')
                return False
            reservation['state'] = 'consumed'
            window = self.windows[reservation['window']]
            window['consumed_units'] += units
            window['consumed_requests'] += 1
            self._event('consumed', identifier, now, window=reservation['window'], units=units)
            return True

    def direct(self, identifier, units):
        with self.lock:
            permission = self.reserve(identifier, units)
            return permission['allowed'] and self.consume(identifier, units), permission

    def release(self, identifier):
        with self.lock:
            now = self.clock()
            self._expire(now)
            reservation = self.reservations.get(identifier)
            if not reservation or reservation['state'] != 'reserved':
                return False
            self._release(identifier, reservation, 'released', now)
            return True

    def snapshot(self):
        with self.lock:
            self._expire(self.clock())
            return {'unit_limit': self.limit, 'request_limit': self.request_limit,
                    'windows': {key: dict(value) for key, value in self.windows.items()},
                    'reservations': {key: dict(value) for key, value in self.reservations.items()},
                    'events': list(self.events)}
