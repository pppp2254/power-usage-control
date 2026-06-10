# Thin wrapper over umqtt.robust. Auto-reconnect + resubscribe, JSON helpers.
try:
    import ujson
except ImportError:
    import json as ujson  # CPython

from umqtt.robust import MQTTClient


class Mqtt:
    def __init__(self, client_id, host, port=1883, user=None, password=None, ssl=False, keepalive=30):
        self._client = MQTTClient(
            client_id=client_id,
            server=host,
            port=port,
            user=user,
            password=password,
            keepalive=keepalive,
            ssl=ssl,
        )
        self._on_message = None
        self._subs = []  # remembered for resubscribe after reconnect

    def connect(self, on_message=None):
        self._on_message = on_message
        self._client.set_callback(self._cb)
        self._client.connect()
        print("[mqtt] connected")

    def _cb(self, topic, payload):
        if self._on_message is None:
            return
        try:
            msg = ujson.loads(payload)
        except Exception:
            msg = payload
        try:
            self._on_message(topic.decode() if isinstance(topic, bytes) else topic, msg)
        except Exception as e:
            print("[mqtt] handler error:", e)

    def subscribe(self, topic, qos=1):
        self._subs.append((topic, qos))
        self._client.subscribe(topic, qos=qos)

    def publish(self, topic, payload, qos=1, retain=False):
        body = ujson.dumps(payload) if not isinstance(payload, (str, bytes)) else payload
        try:
            # umqtt.robust retries/reconnects internally; this guard keeps a
            # persistent broker outage from killing the control loop.
            self._client.publish(topic, body, qos=qos, retain=retain)
        except Exception as e:
            print("[mqtt] publish error:", e)

    def check_msg(self):
        try:
            self._client.check_msg()
        except Exception as e:
            print("[mqtt] check_msg error:", e)
            self._reconnect()

    def _reconnect(self):
        # umqtt.robust's reconnect does NOT restore subscriptions — do it here.
        try:
            self._client.reconnect()
            for topic, qos in self._subs:
                self._client.subscribe(topic, qos=qos)
            print("[mqtt] reconnected + resubscribed")
        except Exception as e:
            print("[mqtt] reconnect failed:", e)
