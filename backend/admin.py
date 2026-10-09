"""Single-owner email/password authentication with persistent hashed sessions."""
import hashlib,hmac,json,os,re,secrets,sqlite3,time
import bridge

def db():
    d=sqlite3.connect(bridge.ROOT/'admin.sqlite',timeout=30);d.row_factory=sqlite3.Row
    d.executescript('''CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY CHECK(id=1),email TEXT,salt TEXT,password_hash TEXT);
    CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,expires INTEGER);
    CREATE TABLE IF NOT EXISTS failures(time INTEGER);''')
    return d

def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=1,maxmem=67108864).hex()

def configured():
    with db() as d:return bool(d.execute('SELECT 1 FROM owner').fetchone())

def setup_allowed(code):
    expected=os.environ.get('ADMIN_SETUP_TOKEN','')
    return len(expected)>=32 and hmac.compare_digest(code,expected) and not configured()

def setup(code,email,password):
    if not setup_allowed(code):raise ValueError('Ссылка уже использована или недействительна')
    email=str(email).strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254:raise ValueError('Введите корректный email')
    if not isinstance(password,str) or not 10<=len(password)<=200:raise ValueError('Пароль: от 10 до 200 символов')
    salt=secrets.token_hex(32);hashed=password_hash(password,salt)
    with db() as d:
        d.execute('INSERT INTO owner(id,email,salt,password_hash) VALUES(1,?,?,?)',(email,salt,hashed))
    return login(email,password)

def login(email,password):
    if not isinstance(password,str) or len(password)>200:raise ValueError('Неверный email или пароль')
    now=int(time.time())
    with db() as d:
        d.execute('DELETE FROM failures WHERE time<?',(now-600,))
        if d.execute('SELECT count(*) FROM failures').fetchone()[0]>=10:raise ValueError('Слишком много попыток. Повторите через 10 минут')
        owner=d.execute('SELECT * FROM owner WHERE id=1').fetchone()
        salt=owner['salt'] if owner else '00'*32
        candidate=password_hash(password,salt)
        valid=bool(owner) and hmac.compare_digest(owner['email'].encode(),str(email).strip().lower().encode()) and hmac.compare_digest(owner['password_hash'],candidate)
        if not valid:
            d.execute('INSERT INTO failures VALUES(?)',(now,));d.commit()
            raise ValueError('Неверный email или пароль')
        token=secrets.token_urlsafe(48);digest=hashlib.sha256(token.encode()).hexdigest()
        d.execute('DELETE FROM sessions WHERE expires<?',(now,))
        d.execute('INSERT INTO sessions VALUES(?,?)',(digest,now+7*86400))
        return token

def authenticated(token):
    if not token:return False
    digest=hashlib.sha256(token.encode()).hexdigest()
    with db() as d:return bool(d.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?',(digest,int(time.time()))).fetchone())

def logout(token):
    with db() as d:d.execute('DELETE FROM sessions WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),))
