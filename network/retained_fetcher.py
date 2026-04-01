# network/retained_fetcher.py
# Fetches retained messages from AWS IoT Core on startup.
#
# AWS IoT Core has a known issue where retained messages are not delivered
# on MQTT subscriptions in some configurations. This module works around that
# by using the AWS IoT Data API with certificate authentication.

import json
import logging
import base64
import requests
from PyQt6.QtCore import QObject, pyqtSignal, QThread

log = logging.getLogger(__name__)


class RetainedFetcher(QThread):
    """Fetches retained messages from AWS IoT Core using certificate auth."""
    
    messages_ready = pyqtSignal(list)  # list of (topic, payload_dict)
    
    def __init__(self, endpoint: str, cert_file: str, key_file: str, ca_cert: str, parent=None):
        super().__init__(parent)
        self._endpoint = endpoint
        self._cert_file = cert_file
        self._key_file = key_file
        self._ca_cert = ca_cert
    
    def run(self):
        """Fetch retained messages in background thread."""
        if not self._endpoint or not self._cert_file or not self._key_file:
            log.info("AWS IoT credentials not configured — skipping retained message fetch")
            self.messages_ready.emit([])
            return
        
        try:
            base_url = f'https://{self._endpoint}'
            
            # List all retained messages
            list_url = f'{base_url}/retainedMessage'
            response = requests.get(
                list_url,
                cert=(self._cert_file, self._key_file),
                verify=self._ca_cert,
                timeout=10
            )
            
            if response.status_code != 200:
                log.warning("Failed to list retained messages: HTTP %d", response.status_code)
                self.messages_ready.emit([])
                return
            
            data = response.json()
            retained_topics = data.get('retainedTopics', [])
            
            # Filter to storm/* topics only
            storm_topics = [
                t['topic'] for t in retained_topics 
                if t['topic'].startswith('storm/')
            ]
            
            log.info("Found %d retained storm topics", len(storm_topics))
            
            # Fetch each message
            messages = []
            for topic in storm_topics:
                try:
                    # URL encode the topic
                    import urllib.parse
                    encoded_topic = urllib.parse.quote(topic, safe='')
                    get_url = f'{base_url}/retainedMessage/{encoded_topic}'
                    
                    response = requests.get(
                        get_url,
                        cert=(self._cert_file, self._key_file),
                        verify=self._ca_cert,
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        msg_data = response.json()
                        payload_bytes = base64.b64decode(msg_data['payload'])
                        payload_dict = json.loads(payload_bytes.decode('utf-8'))
                        messages.append((topic, payload_dict))
                        log.debug("Fetched retained: %s", topic)
                except Exception as e:
                    log.warning("Failed to fetch %s: %s", topic, e)
            
            log.info("Fetched %d retained messages", len(messages))
            self.messages_ready.emit(messages)
            
        except requests.exceptions.RequestException as e:
            log.error("Retained fetch failed: %s", e)
            self.messages_ready.emit([])
        except Exception as e:
            log.error("Retained fetch failed: %s", e)
            self.messages_ready.emit([])
