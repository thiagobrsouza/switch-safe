def register_blueprints(app):
    from . import auth, backups, main, settings, switches, users

    from ..firewalls import views as firewalls

    for module in (auth, main, switches, backups, settings, users, firewalls):
        app.register_blueprint(module.bp)
