"""Explicit operator command to provision the first admin from an existing account."""
import argparse
from sqlalchemy import select
from sqlalchemy.orm import Session
def main():
    from dotenv import load_dotenv
    load_dotenv()
    from app.main import User,engine
    from app.admin import audit
    parser=argparse.ArgumentParser(description='Promote an existing active account to admin; requires database/operator access.')
    parser.add_argument('command',choices=['promote'])
    parser.add_argument('username')
    args=parser.parse_args()
    with Session(engine) as db:
        user=db.scalar(select(User).where(User.username==args.username.lower()).with_for_update())
        if not user:parser.error('Account not found. Sign up in the browser first.')
        if not user.active:parser.error('Suspended account cannot be promoted.')
        previous=user.role;user.role='admin'
        audit(db,'operator-cli','user.promote',user.id,{'username':user.username,'before':previous,'after':'admin','reason':'Explicit operator provisioning'})
        db.commit();print(f'{user.username} is now an admin. Refresh the browser and open /admin.')
if __name__=='__main__':main()
