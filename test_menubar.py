"""Minimal rumps test — does an icon appear?"""
import AppKit
AppKit.NSApplication.sharedApplication().setActivationPolicy_(1)

import rumps

class TestApp(rumps.App):
    def __init__(self):
        super().__init__("TEST", title="TEST")
        self.menu = ["Item 1", "Item 2"]

    @rumps.clicked("Item 1")
    def item1(self, _):
        rumps.notification("Test", "", "It works!")

if __name__ == "__main__":
    print("Starting test app... look for 'TEST' in menubar")
    TestApp().run()
