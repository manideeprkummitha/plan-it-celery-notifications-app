from celery_app import celery_app

if __name__ == '__main__':
    # this will start a celery worker
    argv = [
        'worker',
        '--loglevel=info',
    ]

    # add beat option to run the scheduler in the same process
    # for production you might want to run the beat scheduler separetly
    argv.append('--beat')

    celery_app.worker_main(argv)