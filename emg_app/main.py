"""Package entrypoint for launching the EMG GUI."""

from .app import EMGApp


def main() -> None:
	app = EMGApp()
	app.protocol("WM_DELETE_WINDOW", app.on_close)
	app.mainloop()


if __name__ == "__main__":
	main()
