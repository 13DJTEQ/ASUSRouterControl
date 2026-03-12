"""Pure PyObjC status bar test — no rumps."""
import sys
import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from PyObjCTools import AppHelper


class AppDelegate(NSObject):
    statusbar = None
    statusitem = None

    def applicationDidFinishLaunching_(self, notification):
        print("App launched, creating status item...", flush=True)
        self.statusbar = NSStatusBar.systemStatusBar()
        self.statusitem = self.statusbar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.statusitem.button().setTitle_("PYOBJC_TEST")

        menu = NSMenu.new()
        menu.setAutoenablesItems_(False)

        item = NSMenuItem.new()
        item.setTitle_("Hello from PyObjC")
        menu.addItem_(item)

        quit_item = NSMenuItem.new()
        quit_item.setTitle_("Quit")
        quit_item.setAction_("terminate:")
        menu.addItem_(quit_item)

        self.statusitem.setMenu_(menu)
        print("Status item created. Check menubar for 'PYOBJC_TEST'", flush=True)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    print("Starting event loop...", flush=True)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
