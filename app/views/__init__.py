def register_blueprints(app):
    from . import auth, backups, main, settings, switches, users

    for module in (auth, main, switches, backups, settings, users):
        app.register_blueprint(module.bp)
