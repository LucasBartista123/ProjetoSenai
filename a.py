from app import app, db

def create_tables():
    with app.app_context():
        print("A criar tabelas no banco de dados...")
        db.create_all()
        print("Tabelas criadas com sucesso!")

if __name__ == '__main__':
    create_tables()