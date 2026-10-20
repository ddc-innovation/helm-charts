#!/usr/bin/env python3
"""Recoverable namespace Secret + MySQL account provisioning. No third-party modules.

This is the inspected bdv-provision.py contract bundled with the chart so DBA
operations are reviewable. The bootstrap Job runs it through entrypoint.py.
"""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import tempfile
import time

class ProvisionError(RuntimeError):
    pass

def identifier(value):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', value):
        raise ProvisionError('Database/user identifier must be ASCII and at most 32 characters')
    return value

def literal(value):
    if '\x00' in value or '\n' in value or '\r' in value:
        raise ProvisionError('Control characters are not allowed in credentials')
    return "'" + value.replace("'", "''") + "'"

def option(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'

class MySQL:
    """Keep an advisory lock in the same connection while running guarded operations."""
    def __init__(self, credentials, ca, mode='VERIFY_IDENTITY'):
        self.credentials, self.ca, self.mode = credentials, ca, mode
        self.tmp = None
        self.process = None
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bdv-mysql-')
        p = Path(self.tmp.name)/'client.cnf'
        c = self.credentials
        text = '[client]\n' + '\n'.join(k+'='+option(c[v]) for k,v in [('host','host'),('port','port'),('user','username'),('password','password')])
        text += '\nssl-mode='+self.mode+'\nconnect-timeout=10\n'
        if self.mode in ('VERIFY_CA', 'VERIFY_IDENTITY'):
            if not self.ca:
                raise ProvisionError('CA file required for certificate verification')
            text += 'ssl-ca='+option(self.ca)+'\n'
        p.write_text(text); p.chmod(0o600)
        self.process = subprocess.Popen(['mysql','--defaults-file='+str(p),'--batch','--raw','--skip-column-names','--unbuffered'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self.query("SET SESSION sql_mode='NO_BACKSLASH_ESCAPES'")
        except Exception:
            self.__exit__(None,None,None)
            raise
        return self
    def query(self, sql, timeout=40):
        marker = 'BDV_END_'+secrets.token_hex(16)
        try:
            self.process.stdin.write((sql+';\nSELECT '+literal(marker)+';\n').encode()); self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ProvisionError('MySQL connection failed; check TLS, authentication and server logs') from None
        result, buffer = [], b''
        poll = selectors.DefaultSelector(); poll.register(self.process.stdout, selectors.EVENT_READ)
        deadline=time.monotonic()+timeout
        try:
            while time.monotonic()<deadline:
                if not poll.select(max(0,deadline-time.monotonic())):break
                data=os.read(self.process.stdout.fileno(),65536)
                if not data:raise ProvisionError('MySQL command failed; inspect server logs securely')
                buffer+=data
                while b'\n' in buffer:
                    row,buffer=buffer.split(b'\n',1)
                    row=row.decode().rstrip('\r')
                    if row==marker:return result
                    result.append(row)
            raise ProvisionError('MySQL operation timed out')
        finally:poll.close()
    def lock(self, name):
        if self.query('SELECT GET_LOCK('+literal(name)+', 15)',timeout=25)!=['1']:
            raise ProvisionError('Another initialization holds the database lock; retry later')
    def __exit__(self,*args):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            for stream in [self.process.stdin,self.process.stdout]:
                if stream:stream.close()
        if self.tmp:self.tmp.cleanup()

def kube(namespace, args, payload=None, allow_failure=False):
    p=subprocess.run(['kubectl','--request-timeout=20s','--namespace',namespace,*args],input=None if payload is None else json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if p.returncode and not allow_failure:
        # kubectl errors may echo Secret request bodies. Never include them.
        raise ProvisionError('Kubernetes Secret operation failed; check namespace RBAC and API availability')
    return p

def read_secret(namespace,name):
    p=kube(namespace,['get','secret',name,'--ignore-not-found','-o','json'])
    return json.loads(p.stdout) if p.stdout.strip() else None

def decode(secret):
    try:return {k:base64.b64decode(v,validate=True).decode() for k,v in secret.get('data',{}).items()}
    except (ValueError,UnicodeError):raise ProvisionError('Application Secret has invalid encoded text') from None

def preserve_credentials(namespace,cfg,host,port,account_host):
    name=cfg['secret']; current=read_secret(namespace,name)
    if current is None:
        data={'host':host,'port':str(port),'database':identifier(cfg['database']),'username':identifier(cfg['username']),'password':secrets.token_urlsafe(48),'accountHost':account_host}
        body={'apiVersion':'v1','kind':'Secret','type':'Opaque','metadata':{'name':name,'namespace':namespace,'labels':{'bdv.dinecodetalkers.com/credentials':'retained'},'annotations':{'bdv.dinecodetalkers.com/ownership':'bootstrap-external-retained'}},'stringData':data}
        # Create BEFORE touching MySQL. If create loses a race, get the winner.
        kube(namespace,['create','-f','-'],body,allow_failure=True)
        current=read_secret(namespace,name)
        if current is None:raise ProvisionError('Could not persist generated credentials; no database changes made')
    data=decode(current)
    if not all(data.get(k) for k in ['host','port','database','username','password']):
        raise ProvisionError('Application Secret missing a required key')
    expected={'host':host,'port':str(port),'database':cfg['database'],'username':cfg['username']}
    if any(data[k]!=v for k,v in expected.items()) or data.get('accountHost',account_host)!=account_host:
        raise ProvisionError('Existing application Secret differs from requested identity; refusing automatic changes')
    identifier(data['database']);identifier(data['username']);literal(data['password'])
    return data

def provision(namespace,cfg,admin,host,port,account_host,ca,mode):
    creds=preserve_credentials(namespace,cfg,host,port,account_host)
    database=identifier(creds['database']); user=identifier(creds['username'])
    account=literal(user)+'@'+literal(account_host)
    with MySQL(admin,ca,mode) as db:
        db.lock('bdv-provision-'+hashlib.sha256((host+':'+str(port)+':'+database).encode()).hexdigest()[:40])
        # Existing accounts retain their passwords. Missing accounts are recoverably created.
        db.query('CREATE DATABASE IF NOT EXISTS `'+database+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        db.query('CREATE USER IF NOT EXISTS '+account+' IDENTIFIED BY '+literal(creds['password']))
        # Verify before granting to an existing account whose credentials may differ.
        with MySQL(creds,ca,mode) as app:
            if app.query('SELECT 1')!=['1']:raise ProvisionError('Application login failed; no password reset attempted')
        # Only DB scope, no GRANT OPTION. DDL is required by schema tooling.
        db.query('GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE ON `'+database+'`.* TO '+account)
        with MySQL(creds,ca,mode) as app:
            if app.query('USE `'+database+'`; SELECT 1')!=['1']:raise ProvisionError('Application database verification failed')
    fingerprint=hashlib.sha256(json.dumps({k:creds[k] for k in ['host','port','database','username']},sort_keys=True).encode()).hexdigest()
    patch={'metadata':{'annotations':{'bdv.dinecodetalkers.com/login-verified-at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'bdv.dinecodetalkers.com/identity-sha256':fingerprint}}}
    kube(namespace,['patch','secret',cfg['secret'],'--type=merge','--patch-file=/dev/stdin'],patch)
    print('PASS: '+cfg['secret']+' persisted, provisioned and application database login verified',flush=True)

def main():
    os.umask(0o077)
    namespace=os.environ['NAMESPACE'];host=os.environ['MYSQL_HOST'];port=int(os.environ.get('MYSQL_PORT','3306'))
    account_host=os.environ['MYSQL_ACCOUNT_HOST'];mode=os.environ.get('MYSQL_SSL_MODE','VERIFY_IDENTITY')
    if not re.fullmatch(r'[A-Za-z0-9_.:%/-]{1,255}',account_host) or account_host=='%':
        raise ProvisionError('Set an explicit restricted MySQL account host; unrestricted % is refused')
    if mode not in ['VERIFY_IDENTITY','VERIFY_CA','REQUIRED']:raise ProvisionError('Unsupported TLS mode')
    if not host or not 0<port<65536:raise ProvisionError('Invalid database endpoint')
    configs=json.loads(os.environ['DATABASES_JSON'])
    if set(configs)!= {'hive','ranger'}:raise ProvisionError('Exactly Hive and Ranger database configurations required')
    for field in ['database','username','secret']:
        if configs['hive'][field]==configs['ranger'][field]:raise ProvisionError('Hive and Ranger must use separate identities')
    admin={'host':host,'port':str(port),'username':Path('/admin/username').read_text().strip(),'password':Path('/admin/password').read_text()}
    for cfg in configs.values():provision(namespace,cfg,admin,host,port,account_host,os.environ.get('MYSQL_CA',''),mode)

if __name__=='__main__':
    try:main()
    except (ProvisionError,KeyError,ValueError,OSError,subprocess.SubprocessError) as exc:
        # Never print arbitrary subprocess error text, credential content, or SQL.
        print('FAIL: '+str(exc) if isinstance(exc,ProvisionError) else 'FAIL: provisioning input/tool failure; inspect prerequisites securely',flush=True)
        raise SystemExit(1)
